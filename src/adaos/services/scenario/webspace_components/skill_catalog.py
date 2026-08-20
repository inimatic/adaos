from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import yaml

from adaos.services.skill.runtime_env import SkillRuntimeEnvironment


@dataclass(frozen=True, slots=True)
class WebspaceSkillCatalogOperations:
    apply_node_context_to_ui: Any
    apply_webui_load_hint: Any
    cache_state: Any
    catalog_entry_is_foreign_relay: Any
    clone_json_like: Any
    coerce_dict: Any
    detached_member_node_ids: Any
    fingerprint_json_like: Any
    get_local_capacity: Any
    load_config: Any
    local_node_id: Any
    logger: Any
    looks_like_skill_ui_interface: Any
    mark_modal_def: Any
    member_device_inventory_display_map: Any
    node_scope_data_path: Any
    node_scoped_catalog_id: Any
    node_scoped_data_path_node_id: Any
    node_scoped_modal_ids: Any
    normalize_webio_receiver: Any
    normalize_webui_modal_def: Any
    remote_member_node_display: Any
    scenario_exists_for_switch: Any
    scope_remote_catalog_entry_id: Any
    skill_decls_cache_ttl_s: Any


class WebspaceSkillCatalogService:
    @staticmethod
    def _active_runtime_source(paths: Any, skill_name: str) -> dict[str, Any] | None:
        """Resolve declarations from the same immutable slot that runs the skill."""

        try:
            skills_root = Path(paths.skills_dir()).expanduser().resolve()
            environment = SkillRuntimeEnvironment(
                skills_root=skills_root,
                skill_name=skill_name,
            )
            version = str(environment.resolve_active_version() or "").strip()
            if not version:
                return None
            slot = str(environment.read_active_slot(version) or "").strip().upper()
            if slot not in {"A", "B"}:
                return None
            slot_paths = environment.build_slot_paths(version, slot)
            slot_root = slot_paths.root.expanduser().resolve()
            skill_dir = (slot_paths.src_dir / "skills" / skill_name).resolve()
            if skill_dir != slot_root and slot_root not in skill_dir.parents:
                return None
            resolved_manifest = slot_paths.resolved_manifest.expanduser().resolve()
            if not skill_dir.is_dir() or not resolved_manifest.is_file():
                return None
            source_manifest = skill_dir / "skill.yaml"
            return {
                "skill_dir": skill_dir,
                "manifest_path": source_manifest if source_manifest.is_file() else resolved_manifest,
                "resolved_manifest": resolved_manifest,
                "version": version,
                "slot": slot,
            }
        except (OSError, RuntimeError, ValueError):
            return None

    def load_webui(
        self,
        runtime: Any,
        operations: WebspaceSkillCatalogOperations,
        skill_name: str,
        space: str,
        *,
        log_missing: bool = False,
    ) -> dict[str, Any] | None:
        paths = runtime.ctx.paths
        base = paths.dev_skills_dir() if space == "dev" else paths.skills_dir()
        skill_dir = Path(base) / skill_name
        manifest_path = skill_dir / "skill.yaml"
        source_authority = "dev_workspace" if space == "dev" else "workspace_source"
        runtime_source = self._active_runtime_source(paths, skill_name) if space != "dev" else None
        if runtime_source is not None:
            skill_dir = Path(runtime_source["skill_dir"])
            manifest_path = Path(runtime_source["manifest_path"])
            source_authority = "active_runtime_slot"

        path = skill_dir / "webui.json"
        if not path.exists() and runtime_source is None:
            try:
                repo_root_attr = getattr(paths, "repo_root", None)
                repo_root = repo_root_attr() if callable(repo_root_attr) else repo_root_attr
                if repo_root:
                    fallback_dir = (
                        Path(repo_root).expanduser().resolve() / ".adaos" / "workspace" / "skills" / skill_name
                    )
                    fallback = fallback_dir / "webui.json"
                    if fallback.exists():
                        path = fallback
                        manifest_path = fallback_dir / "skill.yaml"
                        source_authority = "repo_workspace_fallback"
            except Exception:
                pass
        if not path.exists():
            operations.cache_state.discard_webui_declaration(str(path))
            if log_missing and operations.logger.isEnabledFor(logging.DEBUG):
                stack = " <- ".join(
                    f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}"
                    for frame in traceback.extract_stack(limit=8)[:-1]
                )
                operations.logger.debug("webui.json missing for %s (%s) caller=%s", skill_name, space, stack)
            return {}
        cache_key = str(path.resolve())
        try:
            stat = path.stat()
            stamp_parts: list[Any] = [cache_key, int(stat.st_mtime_ns), int(stat.st_size)]
            if manifest_path.exists():
                manifest_stat = manifest_path.stat()
                stamp_parts.extend(
                    [
                        str(manifest_path.resolve()),
                        int(manifest_stat.st_mtime_ns),
                        int(manifest_stat.st_size),
                    ]
                )
            stamp = tuple(stamp_parts)
        except Exception:
            stamp = None
        if stamp is not None:
            cached = operations.cache_state.get_webui_declaration(cache_key)
            if cached is not None and cached[0] == stamp:
                return cached[1]
        try:
            # Accept UTF-8 with BOM produced by some Windows/PowerShell editors.
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            operations.logger.warning("failed to read webui.json for %s: %s", skill_name, exc)
            if stamp is not None:
                operations.cache_state.put_webui_declaration(cache_key, stamp, {})
            return {}
        if not isinstance(raw, dict):
            operations.logger.warning("webui.json must be an object for %s", skill_name)
            if stamp is not None:
                operations.cache_state.put_webui_declaration(cache_key, stamp, {})
            return {}

        catalog = raw.get("catalog") or {}
        apps = raw.get("apps") or catalog.get("apps") or []
        widgets = raw.get("widgets") or catalog.get("widgets") or []
        resources = raw.get("resources") or catalog.get("resources") or {}
        ui_interface = raw.get("interface") or raw.get("uiInterface") or {}
        registry = raw.get("registry") or {}
        reg_modals_raw = registry.get("modals") or {}
        reg_widgets_raw = registry.get("widgets") or {}
        ydoc_defaults = raw.get("ydoc_defaults") or {}
        raw_contrib = raw.get("contributions") or []
        contributions = [c for c in raw_contrib if isinstance(c, dict)]
        webio_raw = raw.get("webio") or {}
        webio_receivers_raw = webio_raw.get("receivers") if isinstance(webio_raw, dict) else {}
        ui_owner = "shared" if skill_name == "web_desktop_skill" else "node"
        try:
            if manifest_path.exists():
                manifest_raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
                if isinstance(manifest_raw, dict):
                    owner_token = str(manifest_raw.get("webui_owner") or manifest_raw.get("ui_owner") or "").strip().lower()
                    if owner_token in {"shared", "node"}:
                        ui_owner = owner_token
        except Exception:
            operations.logger.debug("failed to read skill manifest ownership for %s", skill_name, exc_info=True)

        payload = {
            "skill": skill_name,
            "space": space,
            "source_path": str(path.parent.resolve()),
            "source_authority": source_authority,
            "node_id": operations.local_node_id(),
            "ui_owner": ui_owner,
            "apps": [operations.apply_webui_load_hint(it) for it in apps if isinstance(it, dict)],
            "widgets": [operations.apply_webui_load_hint(it) for it in widgets if isinstance(it, dict)],
            "resources": operations.coerce_dict(resources),
            "interface": operations.coerce_dict(ui_interface),
            "registry": {
                "modals": (
                    {str(k): operations.normalize_webui_modal_def(v) for k, v in reg_modals_raw.items()}
                    if isinstance(reg_modals_raw, dict)
                    else [str(x) for x in reg_modals_raw if isinstance(x, (str, int))]
                ),
                "widgets": (
                    {str(k): operations.apply_webui_load_hint(v) for k, v in reg_widgets_raw.items()}
                    if isinstance(reg_widgets_raw, dict)
                    else [str(x) for x in reg_widgets_raw if isinstance(x, (str, int))]
                ),
            },
            "ydoc_defaults": ydoc_defaults if isinstance(ydoc_defaults, dict) else {},
            "contributions": contributions,
            "webio": {
                "receivers": (
                    {str(k): operations.normalize_webio_receiver(v) for k, v in webio_receivers_raw.items() if str(k).strip()}
                    if isinstance(webio_receivers_raw, dict)
                    else {}
                ),
            },
        }
        if runtime_source is not None:
            payload["runtime_version"] = str(runtime_source["version"])
            payload["runtime_slot"] = str(runtime_source["slot"])
            payload["resolved_manifest"] = str(runtime_source["resolved_manifest"])
        if stamp is not None:
            operations.cache_state.put_webui_declaration(cache_key, stamp, payload)
        return payload


    def collect_skill_decls(
        self,
        runtime: Any,
        operations: WebspaceSkillCatalogOperations,
        mode: str = "mixed",
        *,
        include_remote: bool = True,
    ) -> list[dict[str, Any]]:
        try:
            cap = operations.get_local_capacity()
            skills = cap.get("skills") or []
        except Exception:
            skills = []
        if not isinstance(skills, list):
            skills = []

        active_records = [
            rec
            for rec in skills
            if isinstance(rec, dict) and rec.get("active", True) and (rec.get("name") or rec.get("id"))
        ]
        selection_evidence: list[dict[str, Any]] = []
        for rec in active_records:
            skill_name = str(rec.get("name") or rec.get("id"))
            runtime_source = self._active_runtime_source(runtime.ctx.paths, skill_name) if mode != "dev" else None
            selection_evidence.append(
                {
                    "name": skill_name,
                    "dev": bool(rec.get("dev")),
                    "runtime_version": str((runtime_source or {}).get("version") or ""),
                    "runtime_slot": str((runtime_source or {}).get("slot") or ""),
                    "resolved_manifest": str((runtime_source or {}).get("resolved_manifest") or ""),
                }
            )
        selection_fingerprint = operations.fingerprint_json_like(selection_evidence)
        cache_key = (
            f"{str(mode or '').strip() or 'mixed'}:{1 if include_remote else 0}:"
            f"{selection_fingerprint}"
        )
        now = time.monotonic()
        cached = operations.cache_state.get_skill_declarations(cache_key)
        if cached is not None and now - float(cached[0]) <= operations.skill_decls_cache_ttl_s():
            try:
                runtime._last_skill_decls_fingerprint = str(cached[1] or "")
            except Exception:
                pass
            try:
                return list(cached[2])
            except Exception:
                return [dict(item) for item in cached[2] if isinstance(item, dict)]

        decls: List[Dict[str, Any]] = []
        for rec in active_records:
            name = rec.get("name") or rec.get("id")
            skill_name = str(name)

            if mode == "workspace":
                # Workspace mode: always use default webui.json regardless of
                # dev flag so that skills remain visible even when a dev
                # variant exists.
                decl = runtime._load_webui(skill_name, "default")
                if decl:
                    decls.append(decl)
                continue

            if mode == "dev":
                # Dev mode: include all active skills but prefer dev webui.json
                # when present, falling back to workspace webui.json.
                decl = runtime._load_webui(skill_name, "dev")
                if not decl:
                    decl = runtime._load_webui(skill_name, "default")
                if decl:
                    decls.append(decl)
                continue

            # Mixed mode: include both dev and default variants as-is.
            space = "dev" if rec.get("dev") else "default"
            decl = runtime._load_webui(skill_name, space)
            if decl:
                decls.append(decl)

        # Always ensure desktop skill's own webui.json is loaded so that
        # base desktop modals remain available even if not listed in capacity.
        try:
            desktop_decl = runtime._load_webui("web_desktop_skill", "default")
        except Exception:
            desktop_decl = {}
        if isinstance(desktop_decl, dict) and desktop_decl:
            decls.append(desktop_decl)

        if include_remote and mode != "dev":
            decls.extend(runtime._collect_remote_skill_decls())

        fingerprint = operations.fingerprint_json_like(decls)
        try:
            runtime._last_skill_decls_fingerprint = fingerprint
        except Exception:
            pass
        operations.cache_state.put_skill_declarations(
            cache_key,
            now,
            fingerprint,
            operations.clone_json_like(decls),
        )
        return decls


    def collect_remote_skill_decls(
        self,
        runtime: Any,
        operations: WebspaceSkillCatalogOperations,
    ) -> list[dict[str, Any]]:
        try:
            conf = operations.load_config()
        except Exception:
            conf = None
        if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
            return []
        try:
            from adaos.services.registry.subnet_directory import get_directory

            nodes = get_directory().list_known_nodes()
        except Exception:
            nodes = []
        local_node_id = operations.local_node_id()
        detached_node_ids = operations.detached_member_node_ids()
        inventory_display = operations.member_device_inventory_display_map()
        decls: List[Dict[str, Any]] = []
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            node_id = str(node.get("node_id") or "").strip()
            if not node_id or node_id == local_node_id:
                continue
            if node_id in detached_node_ids:
                continue
            runtime_projection = (
                node.get("runtime_projection")
                if isinstance(node.get("runtime_projection"), Mapping)
                else {}
            )
            snapshot = (
                runtime_projection.get("snapshot")
                if isinstance(runtime_projection.get("snapshot"), Mapping)
                else {}
            )
            snapshot_node_id = str(snapshot.get("node_id") or "").strip() if isinstance(snapshot, Mapping) else ""
            if snapshot_node_id and snapshot_node_id != node_id:
                continue
            catalog = (
                snapshot.get("desktop_catalog")
                if isinstance(snapshot.get("desktop_catalog"), Mapping)
                else {}
            )
            apps = catalog.get("apps") if isinstance(catalog.get("apps"), list) else []
            widgets = catalog.get("widgets") if isinstance(catalog.get("widgets"), list) else []
            registry = catalog.get("registry") if isinstance(catalog.get("registry"), Mapping) else {}
            resources = catalog.get("resources") if isinstance(catalog.get("resources"), Mapping) else {}
            raw_catalog_interface = catalog.get("interface") if isinstance(catalog.get("interface"), Mapping) else {}
            ui_interface = raw_catalog_interface if operations.looks_like_skill_ui_interface(raw_catalog_interface) else {}
            ui_interfaces = catalog.get("interfaces") if isinstance(catalog.get("interfaces"), Mapping) else {}
            if not ui_interfaces and raw_catalog_interface and not ui_interface:
                ui_interfaces = raw_catalog_interface
            webio = catalog.get("webio") if isinstance(catalog.get("webio"), Mapping) else {}
            ydoc_defaults = catalog.get("ydoc_defaults") if isinstance(catalog.get("ydoc_defaults"), Mapping) else {}
            if not apps and not widgets and not registry and not resources and not ui_interface and not ui_interfaces and not webio and not ydoc_defaults:
                capacity = node.get("capacity") if isinstance(node.get("capacity"), Mapping) else {}
                skills = capacity.get("skills") if isinstance(capacity.get("skills"), list) else []
                fallback_apps: list[dict[str, Any]] = []
                fallback_widgets: list[dict[str, Any]] = []
                fallback_registry: Dict[str, Any] = {"modals": {}, "widgets": {}}
                fallback_resources: Dict[str, Any] = {}
                fallback_interface: Dict[str, Any] = {}
                fallback_interfaces: Dict[str, Any] = {}
                fallback_webio: Dict[str, Any] = {"receivers": {}}
                fallback_ydoc_defaults: Dict[str, Any] = {}
                seen_skills: set[str] = set()
                for rec in skills:
                    if not isinstance(rec, Mapping):
                        continue
                    skill_name = str(rec.get("name") or rec.get("skill") or "").strip()
                    if not skill_name or skill_name in seen_skills:
                        continue
                    seen_skills.add(skill_name)
                    try:
                        local_decl = runtime._load_webui(skill_name, "default")
                    except Exception:
                        local_decl = None
                    if not isinstance(local_decl, Mapping) or not local_decl:
                        continue
                    local_apps = local_decl.get("apps") if isinstance(local_decl.get("apps"), list) else []
                    local_widgets = local_decl.get("widgets") if isinstance(local_decl.get("widgets"), list) else []
                    fallback_apps.extend([dict(item) for item in local_apps if isinstance(item, dict)])
                    fallback_widgets.extend([dict(item) for item in local_widgets if isinstance(item, dict)])
                    local_registry = local_decl.get("registry") if isinstance(local_decl.get("registry"), Mapping) else {}
                    for group in ("modals", "widgets"):
                        src = local_registry.get(group) if isinstance(local_registry.get(group), Mapping) else {}
                        dst = fallback_registry.setdefault(group, {})
                        if isinstance(dst, dict):
                            for key, value in src.items():
                                dst.setdefault(str(key), value)
                    local_resources = local_decl.get("resources") if isinstance(local_decl.get("resources"), Mapping) else {}
                    for key, value in local_resources.items():
                        fallback_resources.setdefault(str(key), value)
                    local_interface = local_decl.get("interface") if isinstance(local_decl.get("interface"), Mapping) else {}
                    if local_interface and not fallback_interface:
                        fallback_interface = operations.clone_json_like(local_interface)
                    if local_interface and skill_name:
                        fallback_interfaces.setdefault(skill_name, operations.clone_json_like(local_interface))
                    local_webio = local_decl.get("webio") if isinstance(local_decl.get("webio"), Mapping) else {}
                    local_receivers = local_webio.get("receivers") if isinstance(local_webio.get("receivers"), Mapping) else {}
                    receivers_dst = fallback_webio.setdefault("receivers", {})
                    if isinstance(receivers_dst, dict):
                        for key, value in local_receivers.items():
                            receivers_dst.setdefault(str(key), value)
                    local_defaults = local_decl.get("ydoc_defaults") if isinstance(local_decl.get("ydoc_defaults"), Mapping) else {}
                    for key, value in local_defaults.items():
                        fallback_ydoc_defaults.setdefault(str(key), value)
                apps = fallback_apps
                widgets = fallback_widgets
                registry = fallback_registry
                resources = fallback_resources
                ui_interface = fallback_interface
                ui_interfaces = fallback_interfaces
                webio = fallback_webio
                ydoc_defaults = fallback_ydoc_defaults
            if not apps and not widgets and not registry and not resources and not ui_interface and not ui_interfaces and not webio and not ydoc_defaults:
                continue
            display = operations.remote_member_node_display(node, inventory_display=inventory_display)
            modal_id_map = operations.node_scoped_modal_ids(registry, node_id=node_id)
            decl: Dict[str, Any] = {
                "skill": f"subnet.member.{node_id}",
                "space": "default",
                "node_id": node_id,
                "apps": [],
                "widgets": [],
                "resources": {},
                "interface": operations.coerce_dict(ui_interface),
                "interfaces": {},
                "registry": {"modals": {}, "widgets": {}},
                "webio": {"receivers": {}},
                "ydoc_defaults": {},
                "contributions": [],
            }
            mod_spec = registry.get("modals") if isinstance(registry.get("modals"), Mapping) else {}
            if isinstance(mod_spec, Mapping):
                for key, value in mod_spec.items():
                    token = str(key or "").strip()
                    if not token:
                        continue
                    scoped_token = modal_id_map.get(token, operations.node_scoped_catalog_id(node_id, token))
                    decl["registry"]["modals"][scoped_token] = operations.mark_modal_def(
                        operations.apply_node_context_to_ui(
                            value,
                            display,
                            node_id=node_id,
                            modal_id_map=modal_id_map,
                            override_node_display=True,
                        ),
                        source=f"skill:subnet.member.{node_id}",
                        skill=f"subnet.member.{node_id}",
                        dev=False,
                    )
            wid_spec = registry.get("widgets") if isinstance(registry.get("widgets"), Mapping) else {}
            if isinstance(wid_spec, Mapping):
                for key, value in wid_spec.items():
                    token = str(key or "").strip()
                    if not token:
                        continue
                    scoped_token = operations.node_scoped_catalog_id(node_id, token)
                    decl["registry"]["widgets"][scoped_token] = operations.apply_node_context_to_ui(
                        value,
                        display,
                        node_id=node_id,
                        modal_id_map=modal_id_map,
                        override_node_display=True,
                    )
            if isinstance(resources, Mapping):
                for key, value in resources.items():
                    token = str(key or "").strip()
                    if token:
                        decl["resources"][token] = operations.clone_json_like(value)
            if isinstance(ui_interfaces, Mapping):
                for key, value in ui_interfaces.items():
                    token = str(key or "").strip()
                    if token and isinstance(value, Mapping):
                        decl["interfaces"][token] = operations.clone_json_like(value)
            webio_receivers = webio.get("receivers") if isinstance(webio.get("receivers"), Mapping) else {}
            if isinstance(webio_receivers, Mapping):
                for key, value in webio_receivers.items():
                    token = str(key or "").strip()
                    if token:
                        decl["webio"]["receivers"][token] = operations.normalize_webio_receiver(value)
            for path, value in ydoc_defaults.items():
                token = str(path or "").strip()
                if token:
                    scoped_node_id = operations.node_scoped_data_path_node_id(token)
                    if scoped_node_id and scoped_node_id != node_id:
                        continue
                    decl["ydoc_defaults"][operations.node_scope_data_path(token, node_id)] = operations.clone_json_like(value)
            for item in apps:
                if not isinstance(item, dict):
                    continue
                if operations.catalog_entry_is_foreign_relay(item, node_id=node_id):
                    continue
                scenario_id = str(item.get("scenario_id") or "").strip()
                if scenario_id and not operations.scenario_exists_for_switch(scenario_id, space="workspace"):
                    continue
                entry = operations.scope_remote_catalog_entry_id(
                    operations.apply_node_context_to_ui(
                        item,
                        display,
                        node_id=node_id,
                        modal_id_map=modal_id_map,
                        override_node_display=True,
                    ),
                    node_id=node_id,
                )
                decl["apps"].append(entry)
                app_id = str(entry.get("id") or "").strip()
                if app_id:
                    decl["contributions"].append(
                        {
                            "extensionPoint": "desktop.apps",
                            "type": "app",
                            "id": app_id,
                            "autoInstall": True,
                        }
                    )
            for item in widgets:
                if not isinstance(item, dict):
                    continue
                if operations.catalog_entry_is_foreign_relay(item, node_id=node_id):
                    continue
                entry = operations.scope_remote_catalog_entry_id(
                    operations.apply_node_context_to_ui(
                        item,
                        display,
                        node_id=node_id,
                        modal_id_map=modal_id_map,
                        override_node_display=True,
                    ),
                    node_id=node_id,
                )
                decl["widgets"].append(entry)
                widget_id = str(entry.get("id") or "").strip()
                if widget_id:
                    decl["contributions"].append(
                        {
                            "extensionPoint": "desktop.widgets",
                            "type": "widget",
                            "id": widget_id,
                            "autoInstall": True,
                        }
                    )
            if (
                decl["apps"]
                or decl["widgets"]
                or decl["resources"]
                or decl["interface"]
                or decl["interfaces"]
                or decl["registry"]["modals"]
                or decl["registry"]["widgets"]
                or decl["webio"]["receivers"]
                or decl["ydoc_defaults"]
            ):
                decls.append(decl)
        return decls

