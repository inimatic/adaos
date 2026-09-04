from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True, slots=True)
class WebspaceResolutionOperations:
    apply_node_context_to_ui: Any
    apply_node_display_to_entry: Any
    build_materialization_snapshot: Any
    clone_json_like: Any
    clone_skill_ui_interface: Any
    merge_skill_ui_interfaces: Any
    coerce_dict: Any
    coerce_live_branch_subset: Any
    decl_is_node_owned: Any
    dedupe_str_list: Any
    default_materialization_required_branches: Any
    deferred_off_focus_load: Any
    describe_webspace_rebuild_state: Any
    detached_member_node_ids: Any
    effective_branch_paths: Any
    elapsed_ms: Any
    fingerprint_json_like: Any
    extract_scenario_sections_from_content: Any
    has_effective_branch_value: Any
    is_y_map_value: Any
    load_config: Any
    local_node_id: Any
    log_webui_contract_issues: Any
    logger: Any
    mapping_items: Any
    mapping_get: Any
    mark_entry: Any
    mark_modal_def: Any
    materialize_scenario_resource_descriptor: Any
    materialize_skill_resource_descriptor: Any
    materialized_system_resource_descriptors: Any
    merge_by_id: Any
    merge_installed_with_auto: Any
    merge_registry_lists: Any
    merge_webio_receivers: Any
    node_display_from_config: Any
    node_scoped_catalog_id: Any
    node_scoped_modal_ids: Any
    normalize_materialization_required_branches: Any
    normalize_overlay_widget_entries: Any
    patch_map_value_from_previous: Any
    preserve_live_remote_catalog_entries: Any
    preserve_live_remote_modals: Any
    preserve_live_remote_registry_tokens: Any
    preserve_live_state_on_rebuild_enabled: Any
    raise_if_rebuild_request_superseded: Any
    read_effective_branch_fingerprints: Any
    read_node_scoped_scenario_entry: Any
    record_timing: Any
    refresh_pinned_widgets_from_catalog_entries: Any
    replace_map_value: Any
    resolved_output_branch_fingerprints: Any
    resolve_scenario_sections_in_doc: Any
    resolver_inputs_type: Any
    resolver_outputs_type: Any
    runtime_environment_payload: Any
    scenario_loader_space: Any
    scenario_materialization_contract: Any
    scenario_supports_catalog_controls: Any
    scenarios_loader: Any
    set_map_value_if_changed: Any
    set_webspace_rebuild_status_if_current: Any
    trust_previous_materialized_branch_fingerprints_enabled: Any
    validate_application_ui_contract: Any
    whole_branch_replace_paths: Any
    write_effective_branch_fingerprints: Any
    workspace_index: Any


def _apply_component_metadata(
    value: Mapping[str, Any],
    *,
    component_type: str,
    component_id: str,
    version: str = "",
    source_authority: str = "",
    component_update: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data = dict(value)
    metadata = dict(data.get("_adaos") or {}) if isinstance(data.get("_adaos"), Mapping) else {}
    component = {
        "type": str(component_type or "").strip(),
        "id": str(component_id or "").strip(),
    }
    metadata["component"] = component
    if version:
        metadata["version"] = str(version)
        data["version"] = str(version)
    if source_authority:
        metadata["sourceAuthority"] = str(source_authority)
        data["source_authority"] = str(source_authority)
    if isinstance(component_update, Mapping) and component_update:
        update = dict(component_update)
        metadata["componentUpdate"] = update
        metadata["releaseStage"] = str(update.get("stage") or "").strip() or None
        data["component_update"] = update
        data["release_stage"] = metadata["releaseStage"]
    else:
        metadata.pop("componentUpdate", None)
        if str(metadata.get("releaseStageSource") or "").strip() != "builder_materialization":
            metadata.pop("releaseStage", None)
            metadata.pop("releaseStageSource", None)
        data.pop("component_update", None)
        data.pop("release_stage", None)
    data["_adaos"] = metadata
    return data


class WebspaceResolutionService:
    def collect_inputs(
        self,
        runtime: Any,
        operations: WebspaceResolutionOperations,
        ydoc: Any,
        webspace_id: str,
        *,
        materialization_identity: Mapping[str, Any] | None = None,
        scenario_id_override: str | None = None,
        skill_decls_override: Any = None,
        skill_decls_fingerprint_override: str | None = None,
        scenario_content_override: Mapping[str, Any] | None = None,
    ) -> Any:
        collect_timings: Dict[str, float] = {}
        runtime._last_collect_inputs_timings_ms = None
        stage_started = time.perf_counter()
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        registry_map = ydoc.get_map("registry")

        scenario_id = (
            str(scenario_id_override or "").strip()
            or str(ui_map.get("current_scenario") or "web_desktop").strip()
            or "web_desktop"
        )
        scenarios_ui = operations.mapping_get(ui_map, "scenarios") or {}
        scenario_ui_entry = operations.read_node_scoped_scenario_entry(scenarios_ui, scenario_id)
        scenario_ui_application = operations.coerce_dict(scenario_ui_entry.get("application") or {})
        scenario_registry_map = operations.mapping_get(registry_map, "scenarios") or {}
        scenario_registry_entry = operations.read_node_scoped_scenario_entry(scenario_registry_map, scenario_id)
        scenario_data_map = operations.mapping_get(data_map, "scenarios") or {}
        scenario_data_entry = operations.read_node_scoped_scenario_entry(scenario_data_map, scenario_id)
        scenario_catalog = operations.coerce_dict(scenario_data_entry.get("catalog") or {})
        operations.record_timing(collect_timings, "collect_inputs_read_doc", stage_started)

        mode = "mixed"
        metadata: Dict[str, Any] = {}
        overlay_snapshot: Dict[str, Any] = {}
        stage_started = time.perf_counter()
        try:
            row = operations.workspace_index.get_workspace(webspace_id)
            if row:
                mode = row.effective_source_mode
                metadata = {
                    "title": row.title,
                    "kind": row.effective_kind,
                    "source_mode": row.effective_source_mode,
                    "home_scenario": row.effective_home_scenario,
                    "is_dev": row.is_dev,
                }
                if getattr(row, "has_ui_overlay", False):
                    overlay_snapshot = {
                        "installed": operations.coerce_dict(getattr(row, "installed_overlay", {}) or {}),
                        "pinnedWidgets": operations.normalize_overlay_widget_entries(
                            getattr(row, "pinned_widgets_overlay", []) or []
                        ),
                        "topbar": list(getattr(row, "topbar_overlay", []) or []),
                        "pageSchema": operations.coerce_dict(getattr(row, "page_schema_overlay", {}) or {}),
                        "iconOrder": list(getattr(row, "icon_order_overlay", []) or []),
                        "widgetOrder": list(getattr(row, "widget_order_overlay", []) or []),
                        "hiddenSections": list(getattr(row, "hidden_sections_overlay", []) or []),
                        "source": "workspace_manifest_overlay",
                    }
        except Exception:
            mode = "mixed"
            metadata = {}
        operations.record_timing(collect_timings, "collect_inputs_manifest", stage_started)

        stage_started = time.perf_counter()
        if isinstance(scenario_content_override, Mapping) and scenario_content_override:
            scenario_app_ui, base_catalog, registry_entry = operations.extract_scenario_sections_from_content(
                scenario_content_override
            )
            scenario_source = "builder_preview_override"
            legacy_fallback = False
        else:
            scenario_app_ui, base_catalog, registry_entry, scenario_source, legacy_fallback = operations.resolve_scenario_sections_in_doc(
                ydoc,
                webspace_id=webspace_id,
                scenario_id=scenario_id,
                source_mode=mode,
            )
        operations.record_timing(collect_timings, "collect_inputs_scenario_sections", stage_started)
        if metadata:
            metadata = dict(metadata)
        metadata["scenario_source"] = scenario_source
        metadata["legacy_scenario_fallback"] = legacy_fallback
        metadata["materialization"] = operations.scenario_materialization_contract(
            scenario_id,
            source_mode=mode,
            identity=materialization_identity,
        )

        preserve_live_state = operations.preserve_live_state_on_rebuild_enabled()
        stage_started = time.perf_counter()
        if preserve_live_state:
            live_application = operations.coerce_live_branch_subset(
                operations.mapping_get(ui_map, "application") or {},
                ("modals", "interfaces"),
            )
            live_catalog = operations.coerce_live_branch_subset(
                operations.mapping_get(data_map, "catalog") or {},
                ("apps", "widgets"),
            )
            live_registry = operations.coerce_live_branch_subset(
                operations.mapping_get(registry_map, "merged") or {},
                ("modals", "widgets"),
            )
            live_desktop = operations.coerce_live_branch_subset(
                operations.mapping_get(data_map, "desktop") or {},
                ("installed", "topbar", "pageSchema", "pinnedWidgets", "iconOrder", "widgetOrder", "hiddenSections"),
            )
            live_routing = operations.coerce_live_branch_subset(
                operations.mapping_get(data_map, "routing") or {},
                ("routes",),
            )
        else:
            live_application = {}
            live_catalog = {}
            live_registry = {}
            live_desktop = {}
            live_routing = {}
        operations.record_timing(collect_timings, "collect_inputs_live_state", stage_started)

        stage_started = time.perf_counter()
        if skill_decls_override is None:
            try:
                runtime._last_skill_decls_fingerprint = ""
            except Exception:
                pass
            skill_decls = runtime._collect_skill_decls(mode=mode)
            skill_decls_fingerprint = str(getattr(runtime, "_last_skill_decls_fingerprint", "") or "").strip()
        else:
            skill_decls = [dict(item) for item in skill_decls_override if isinstance(item, Mapping)]
            skill_decls_fingerprint = str(skill_decls_fingerprint_override or "").strip()
            if not skill_decls_fingerprint:
                skill_decls_fingerprint = operations.fingerprint_json_like(skill_decls)
            runtime._last_skill_decls_fingerprint = skill_decls_fingerprint
        operations.record_timing(collect_timings, "collect_inputs_skill_decls", stage_started)

        stage_started = time.perf_counter()
        desktop_scenarios = runtime._list_desktop_scenarios(space=mode)
        operations.record_timing(collect_timings, "collect_inputs_desktop_scenarios", stage_started)
        runtime._last_collect_inputs_timings_ms = collect_timings

        # Resolver work continues in the materialization CPU executor.  Never
        # let thread-affine y_py values escape the owner loop through this
        # boundary: shallow ``dict(...)`` conversion can retain nested YMap or
        # YArray objects and make their parent YDoc finalize on the worker.
        detached_live_state = operations.coerce_dict(
            operations.clone_json_like(
                {
                    "application": live_application,
                    "catalog": live_catalog,
                    "registry": live_registry,
                    "desktop": live_desktop,
                    "routing": live_routing,
                }
            )
        )
        detached_skill_decls = operations.clone_json_like(skill_decls)

        return operations.resolver_inputs_type(
            webspace_id=webspace_id,
            scenario_id=str(scenario_id),
            source_mode=mode,
            metadata=operations.coerce_dict(operations.clone_json_like(metadata)),
            scenario_application=operations.coerce_dict(operations.clone_json_like(scenario_app_ui)),
            scenario_catalog=operations.coerce_dict(operations.clone_json_like(base_catalog)),
            scenario_registry=operations.coerce_dict(operations.clone_json_like(registry_entry)),
            overlay_snapshot=operations.coerce_dict(operations.clone_json_like(overlay_snapshot)),
            live_state=detached_live_state,
            compatibility_cache_presence={
                "scenario_ui_application": bool(scenario_ui_application),
                "scenario_registry_entry": bool(scenario_registry_entry),
                "scenario_catalog": bool(scenario_catalog),
            },
            skill_decls=[dict(item) for item in detached_skill_decls if isinstance(item, Mapping)]
            if isinstance(detached_skill_decls, list)
            else [],
            skill_decls_fingerprint=skill_decls_fingerprint,
            desktop_scenarios=desktop_scenarios,
            scenario_source=scenario_source,
            legacy_scenario_fallback=legacy_fallback,
        )


    def resolve(self, runtime: Any, operations: WebspaceResolutionOperations, inputs: Any) -> Any:
        scenario_id = str(inputs.scenario_id or "").strip() or "web_desktop"
        source_mode = str(inputs.source_mode or "").strip() or "mixed"
        scenario_application = operations.coerce_dict(inputs.scenario_application or {})
        scenario_desktop = operations.coerce_dict(scenario_application.get("desktop") or {})
        scenario_catalog = operations.coerce_dict(inputs.scenario_catalog or {})
        scenario_registry = operations.coerce_dict(inputs.scenario_registry or {})
        scenario_apps = [it for it in (scenario_catalog.get("apps") or []) if isinstance(it, Mapping)]
        scenario_widgets = [it for it in (scenario_catalog.get("widgets") or []) if isinstance(it, Mapping)]
        raw_scenario_resources = operations.coerce_dict(
            scenario_application.get("resources") or scenario_catalog.get("resources") or {}
        )
        scenario_resources: Dict[str, Any] = {}
        scenario_space = operations.scenario_loader_space(source_mode)
        try:
            scenario_dir = operations.scenarios_loader.scenario_root_for_space(scenario_id, scenario_space)
        except Exception:
            scenario_dir = None
        for key, value in raw_scenario_resources.items():
            token = str(key or "").strip()
            if token:
                scenario_resources[token] = operations.materialize_scenario_resource_descriptor(
                    token,
                    value,
                    scenario_id=scenario_id,
                    scenario_dir=scenario_dir,
                )
        base_registry_modals = [str(x) for x in (scenario_registry.get("modals") or [])]
        base_registry_widgets = [str(x) for x in (scenario_registry.get("widgets") or [])]

        skill_decls = list(inputs.skill_decls or [])
        try:
            from adaos.services.component_updates import ComponentUpdateService

            component_updates = ComponentUpdateService()
        except Exception:
            component_updates = None
        scenario_update = (
            component_updates.current_component_metadata("scenario", scenario_id)
            if component_updates is not None
            else None
        )
        try:
            scenario_manifest = operations.coerce_dict(
                operations.scenarios_loader.read_manifest(scenario_id, space=scenario_space)
            )
        except Exception:
            scenario_manifest = {}
        scenario_version = str(
            scenario_application.get("version")
            or inputs.metadata.get("scenario_version")
            or scenario_manifest.get("version")
            or ""
        ).strip()
        skill_apps: List[Dict[str, Any]] = []
        skill_widgets: List[Dict[str, Any]] = []
        skill_resources: Dict[str, Any] = {}
        skill_interfaces: Dict[str, Any] = {}
        skill_registry_modals: List[List[str]] = []
        skill_registry_widgets: List[List[str]] = []
        auto_widget_ids: set[str] = set()
        auto_app_ids: set[str] = set()
        active_remote_node_ids: set[str] = set()
        detached_remote_node_ids = operations.detached_member_node_ids()
        local_display = operations.node_display_from_config(operations.load_config())

        for decl in skill_decls:
            skill_name = decl.get("skill") or ""
            space = decl.get("space") or "default"
            node_id = str(decl.get("node_id") or "").strip()
            node_owned = operations.decl_is_node_owned(decl)
            if node_id and str(skill_name or "").strip().startswith("subnet.member."):
                active_remote_node_ids.add(node_id)
            decl_display = {
                "node_label": str(decl.get("node_label") or "").strip(),
                "node_compact_label": str(decl.get("node_compact_label") or "").strip(),
                "node_color": str(decl.get("node_color") or "").strip(),
                "node_index": decl.get("node_index"),
            }
            if not any(decl_display.values()):
                decl_display = local_display
            source = f"skill:{skill_name}"
            dev_flag = space == "dev"
            skill_version = str(decl.get("runtime_version") or decl.get("version") or "").strip()
            skill_source_authority = str(decl.get("source_authority") or "").strip()
            skill_update = (
                dict(decl.get("component_update"))
                if isinstance(decl.get("component_update"), Mapping)
                else None
            )
            reg = decl.get("registry") or {}
            modal_id_map = operations.node_scoped_modal_ids(reg, node_id=node_id) if node_owned else {}
            for app in decl.get("apps") or []:
                if isinstance(app, dict):
                    entry = operations.mark_entry(app, source=source, dev=dev_flag)
                    entry = _apply_component_metadata(
                        entry,
                        component_type="skill",
                        component_id=str(skill_name),
                        version=skill_version,
                        source_authority=skill_source_authority,
                        component_update=skill_update,
                    )
                    if node_owned and node_id:
                        entry = operations.apply_node_context_to_ui(entry, decl_display, node_id=node_id, modal_id_map=modal_id_map)
                    skill_apps.append(operations.apply_node_display_to_entry(entry, decl_display, node_id=node_id))
            for widget in decl.get("widgets") or []:
                if isinstance(widget, dict):
                    entry = operations.mark_entry(widget, source=source, dev=dev_flag)
                    entry = _apply_component_metadata(
                        entry,
                        component_type="skill",
                        component_id=str(skill_name),
                        version=skill_version,
                        source_authority=skill_source_authority,
                        component_update=skill_update,
                    )
                    if node_owned and node_id:
                        entry = operations.apply_node_context_to_ui(entry, decl_display, node_id=node_id, modal_id_map=modal_id_map)
                    skill_widgets.append(operations.apply_node_display_to_entry(entry, decl_display, node_id=node_id))
            raw_resources = decl.get("resources") if isinstance(decl.get("resources"), Mapping) else {}
            skill_source_path = str(decl.get("source_path") or "").strip() or None
            for key, value in raw_resources.items():
                token = str(key or "").strip()
                if token and token not in skill_resources:
                    skill_resources[token] = operations.materialize_skill_resource_descriptor(
                        token,
                        value,
                        skill_name=skill_name,
                        skill_dir=skill_source_path,
                    )
            raw_interface = decl.get("interface") if isinstance(decl.get("interface"), Mapping) else {}
            if raw_interface and skill_name:
                interface_copy = operations.clone_skill_ui_interface(raw_interface, skill=str(skill_name), source=source)
                if interface_copy:
                    skill_key = str(skill_name)
                    skill_interfaces[skill_key] = operations.merge_skill_ui_interfaces(
                        skill_interfaces.get(skill_key),
                        interface_copy,
                    )
            raw_interfaces = decl.get("interfaces") if isinstance(decl.get("interfaces"), Mapping) else {}
            for interface_skill, raw_skill_interface in raw_interfaces.items():
                interface_skill_name = str(interface_skill or "").strip()
                if not interface_skill_name or not isinstance(raw_skill_interface, Mapping):
                    continue
                interface_copy = operations.clone_skill_ui_interface(
                    raw_skill_interface,
                    skill=interface_skill_name,
                    source=f"skill:{interface_skill_name}",
                )
                if interface_copy:
                    skill_interfaces[interface_skill_name] = operations.merge_skill_ui_interfaces(
                        skill_interfaces.get(interface_skill_name),
                        interface_copy,
                    )
            mod_spec = reg.get("modals") or {}
            if isinstance(mod_spec, dict):
                skill_registry_modals.append([modal_id_map.get(str(k), str(k)) for k in mod_spec.keys()])
            else:
                skill_registry_modals.append([str(x) for x in mod_spec])
            wid_spec = reg.get("widgets") or {}
            if isinstance(wid_spec, dict):
                skill_registry_widgets.append([
                    operations.node_scoped_catalog_id(node_id, str(k)) if node_owned and node_id else str(k)
                    for k in wid_spec.keys()
                ])
            else:
                skill_registry_widgets.append([str(x) for x in wid_spec])
            for contrib in decl.get("contributions") or []:
                if not isinstance(contrib, dict):
                    continue
                ep = str(contrib.get("extensionPoint") or "")
                ctype = str(contrib.get("type") or "")
                cid = str(contrib.get("id") or "")
                auto = bool(contrib.get("autoInstall"))
                if not cid or not auto:
                    continue
                if ep == "desktop.widgets" and ctype == "widget":
                    auto_widget_ids.add(cid)
                if ep == "desktop.apps" and ctype == "app":
                    auto_app_ids.add(cid)

        merged_apps = [
            operations.apply_node_display_to_entry(
                _apply_component_metadata(
                    operations.mark_entry(it, source=f"scenario:{scenario_id}", dev=False),
                    component_type="scenario",
                    component_id=scenario_id,
                    version=scenario_version,
                    component_update=scenario_update,
                ),
                local_display,
                node_id=operations.local_node_id(),
            )
            for it in scenario_apps
        ]
        merged_widgets = [
            operations.apply_node_display_to_entry(
                _apply_component_metadata(
                    operations.mark_entry(it, source=f"scenario:{scenario_id}", dev=False),
                    component_type="scenario",
                    component_id=scenario_id,
                    version=scenario_version,
                    component_update=scenario_update,
                ),
                local_display,
                node_id=operations.local_node_id(),
            )
            for it in scenario_widgets
        ]

        extra_apps: List[Dict[str, Any]] = []
        for sid, title in inputs.desktop_scenarios:
            if sid == scenario_id:
                continue
            app_id = f"scenario:{sid}"
            extra_apps.append(
                operations.apply_node_display_to_entry(
                    _apply_component_metadata(
                        operations.mark_entry(
                            {
                                "id": app_id,
                                "title": title,
                                "icon": "apps-outline",
                                "scenario_id": sid,
                            },
                            source=f"scenario:{sid}",
                            dev=False,
                        ),
                        component_type="scenario",
                        component_id=sid,
                        component_update=(
                            component_updates.current_component_metadata("scenario", sid)
                            if component_updates is not None
                            else None
                        ),
                    ),
                    local_display,
                    node_id=operations.local_node_id(),
                )
            )
            auto_app_ids.add(app_id)

        merged_apps = operations.merge_by_id(merged_apps + extra_apps + skill_apps)
        merged_widgets = operations.merge_by_id(merged_widgets + skill_widgets)
        merged_resources = {
            **operations.materialized_system_resource_descriptors(),
            **scenario_resources,
            **skill_resources,
        }
        live_catalog = operations.coerce_dict((inputs.live_state or {}).get("catalog") or {})
        merged_apps = operations.preserve_live_remote_catalog_entries(
            merged_apps,
            current_items=live_catalog.get("apps"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )
        merged_widgets = operations.preserve_live_remote_catalog_entries(
            merged_widgets,
            current_items=live_catalog.get("widgets"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )
        supports_catalog_controls = operations.scenario_supports_catalog_controls(
            scenario_id,
            scenario_application,
        )
        default_modal_ids = ["scenario_switcher"]
        if supports_catalog_controls:
            default_modal_ids = ["apps_catalog", "widgets_catalog", *default_modal_ids]
        merged_registry = {
            "modals": operations.merge_registry_lists(
                base_registry_modals,
                skill_registry_modals + [default_modal_ids],
            ),
            "widgets": operations.merge_registry_lists(base_registry_widgets, skill_registry_widgets),
        }

        installed_current = operations.coerce_dict((inputs.overlay_snapshot or {}).get("installed") or {})
        overlay_has_pinned_widgets = "pinnedWidgets" in (inputs.overlay_snapshot or {})
        overlay_pinned_widgets = operations.normalize_overlay_widget_entries((inputs.overlay_snapshot or {}).get("pinnedWidgets"))
        overlay_icon_order = operations.dedupe_str_list((inputs.overlay_snapshot or {}).get("iconOrder"))
        overlay_widget_order = operations.dedupe_str_list((inputs.overlay_snapshot or {}).get("widgetOrder"))
        overlay_hidden_sections = operations.dedupe_str_list((inputs.overlay_snapshot or {}).get("hiddenSections"))
        scenario_pinned_widgets = operations.normalize_overlay_widget_entries(scenario_desktop.get("pinnedWidgets"))
        scenario_topbar = list(scenario_desktop.get("topbar") or []) if isinstance(scenario_desktop.get("topbar"), list) else []
        scenario_page_schema = operations.coerce_dict(scenario_desktop.get("pageSchema") or {})
        installed_with_auto = operations.merge_installed_with_auto(
            installed_current,
            auto_apps=auto_app_ids,
            auto_widgets=auto_widget_ids,
        )

        merged_modals_map: Dict[str, Any] = {}
        base_modals_map = operations.coerce_dict(scenario_application.get("modals") or {})
        for key, value in base_modals_map.items():
            merged_modals_map[str(key)] = value
        for decl in skill_decls:
            reg = decl.get("registry") or {}
            mod_spec = reg.get("modals") or {}
            if not isinstance(mod_spec, dict):
                continue
            skill_name = str(decl.get("skill") or "").strip()
            node_id = str(decl.get("node_id") or "").strip()
            node_owned = operations.decl_is_node_owned(decl)
            decl_display = {
                "node_label": str(decl.get("node_label") or "").strip(),
                "node_compact_label": str(decl.get("node_compact_label") or "").strip(),
                "node_color": str(decl.get("node_color") or "").strip(),
                "node_index": decl.get("node_index"),
            }
            if not any(decl_display.values()):
                decl_display = local_display
            modal_id_map = operations.node_scoped_modal_ids(reg, node_id=node_id) if node_owned else {}
            for key, value in mod_spec.items():
                raw_token = str(key)
                token = modal_id_map.get(raw_token, raw_token)
                if token and token not in merged_modals_map:
                    modal_def = (
                        operations.apply_node_context_to_ui(value, decl_display, node_id=node_id, modal_id_map=modal_id_map)
                        if node_owned and node_id
                        else value
                    )
                    merged_modals_map[token] = operations.mark_modal_def(
                        modal_def,
                        source=f"skill:{skill_name}" if skill_name else "skill:unknown",
                        skill=skill_name,
                        dev=str(decl.get("space") or "default").strip().lower() == "dev",
                    )
                    merged_modals_map[token] = _apply_component_metadata(
                        merged_modals_map[token],
                        component_type="skill",
                        component_id=skill_name,
                        version=str(decl.get("runtime_version") or decl.get("version") or "").strip(),
                        source_authority=str(decl.get("source_authority") or "").strip(),
                        component_update=(
                            dict(decl.get("component_update"))
                            if isinstance(decl.get("component_update"), Mapping)
                            else None
                        ),
                    )

        if supports_catalog_controls and "apps_catalog" not in merged_modals_map:
            merged_modals_map["apps_catalog"] = {
                "title": "Available Apps",
                "load": dict(operations.deferred_off_focus_load),
                "schema": {
                    "id": "apps_catalog",
                    "load": dict(operations.deferred_off_focus_load),
                    "layout": {
                        "type": "single",
                        "areas": [{"id": "main", "role": "main"}],
                    },
                    "widgets": [
                        {
                            "id": "apps-list",
                            "type": "collection.grid",
                            "area": "main",
                            "title": "Apps",
                            "load": dict(operations.deferred_off_focus_load),
                            "dataSource": {
                                "kind": "y",
                                "path": "data/catalog/apps",
                            },
                            "actions": [
                                {
                                    "on": "select",
                                    "type": "callHost",
                                    "target": "desktop.toggleInstall",
                                    "params": {
                                        "type": "app",
                                        "id": "$event.id",
                                    },
                                }
                            ],
                        }
                    ],
                },
            }
        if supports_catalog_controls and "widgets_catalog" not in merged_modals_map:
            merged_modals_map["widgets_catalog"] = {
                "title": "Available Widgets",
                "load": dict(operations.deferred_off_focus_load),
                "schema": {
                    "id": "widgets_catalog",
                    "load": dict(operations.deferred_off_focus_load),
                    "layout": {
                        "type": "single",
                        "areas": [{"id": "main", "role": "main"}],
                    },
                    "widgets": [
                        {
                            "id": "widgets-list",
                            "type": "collection.grid",
                            "area": "main",
                            "title": "Widgets",
                            "load": dict(operations.deferred_off_focus_load),
                            "dataSource": {
                                "kind": "y",
                                "path": "data/catalog/widgets",
                            },
                            "actions": [
                                {
                                    "on": "select",
                                    "type": "callHost",
                                    "target": "desktop.toggleInstall",
                                    "params": {
                                        "type": "widget",
                                        "id": "$event.id",
                                    },
                                }
                            ],
                        }
                    ],
                },
            }

        live_application = operations.coerce_dict((inputs.live_state or {}).get("application") or {})
        merged_modals_map = operations.preserve_live_remote_modals(
            merged_modals_map,
            current_modals=live_application.get("modals"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )

        live_registry = operations.coerce_dict((inputs.live_state or {}).get("registry") or {})
        merged_registry["modals"] = operations.preserve_live_remote_registry_tokens(
            list(merged_registry.get("modals") or []),
            current_tokens=live_registry.get("modals"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )
        merged_registry["widgets"] = operations.preserve_live_remote_registry_tokens(
            list(merged_registry.get("widgets") or []),
            current_tokens=live_registry.get("widgets"),
            active_remote_node_ids=active_remote_node_ids,
            detached_remote_node_ids=detached_remote_node_ids,
        )

        app_with_modals: Dict[str, Any] = _apply_component_metadata(
            scenario_application,
            component_type="scenario",
            component_id=scenario_id,
            version=scenario_version,
            component_update=scenario_update,
        )
        if merged_modals_map:
            app_with_modals["modals"] = merged_modals_map
        if merged_resources:
            app_with_modals["resources"] = merged_resources
        if skill_interfaces:
            merged_interfaces = operations.coerce_dict(app_with_modals.get("interfaces") or {})
            for key, value in skill_interfaces.items():
                interface_key = str(key)
                merged_interfaces[interface_key] = operations.merge_skill_ui_interfaces(
                    merged_interfaces.get(interface_key),
                    value,
                )
            app_with_modals["interfaces"] = merged_interfaces
        desktop_config = operations.coerce_dict(app_with_modals.get("desktop") or {})
        desktop_config["topbar"] = scenario_topbar
        desktop_config["pageSchema"] = _apply_component_metadata(
            scenario_page_schema,
            component_type="scenario",
            component_id=scenario_id,
            version=scenario_version,
            component_update=scenario_update,
        )
        pinned_widgets_source = overlay_pinned_widgets if overlay_has_pinned_widgets else scenario_pinned_widgets
        desktop_config["pinnedWidgets"] = operations.refresh_pinned_widgets_from_catalog_entries(
            pinned_widgets_source,
            merged_widgets,
        )
        desktop_config["iconOrder"] = list(overlay_icon_order)
        desktop_config["widgetOrder"] = list(overlay_widget_order)
        desktop_config["hiddenSections"] = list(overlay_hidden_sections)
        app_with_modals["desktop"] = desktop_config
        webui_contract_issues = operations.validate_application_ui_contract(
            app_with_modals,
            source=f"webspace:{inputs.webspace_id}:ui.application",
        )
        if webui_contract_issues:
            diagnostics = operations.coerce_dict(app_with_modals.get("diagnostics") or {})
            diagnostics["webui_contract"] = {
                "schema": "adaos.ui.webui_contract.diagnostics.v1",
                "status": "invalid"
                if any(issue.level == "error" for issue in webui_contract_issues)
                else "warning",
                "issue_count": len(webui_contract_issues),
                "error_count": sum(1 for issue in webui_contract_issues if issue.level == "error"),
                "warning_count": sum(1 for issue in webui_contract_issues if issue.level == "warning"),
                "issues": [issue.to_dict() for issue in webui_contract_issues[:40]],
            }
            app_with_modals["diagnostics"] = diagnostics
            operations.log_webui_contract_issues(
                webui_contract_issues,
                webspace_id=inputs.webspace_id,
                source="webspace.materialization",
            )

        desktop_next = operations.coerce_dict((inputs.live_state or {}).get("desktop") or {})
        desktop_installed = operations.coerce_dict(desktop_next.get("installed") or {})
        desktop_installed["apps"] = list(installed_with_auto.get("apps") or [])
        desktop_installed["widgets"] = list(installed_with_auto.get("widgets") or [])
        desktop_next["installed"] = desktop_installed
        desktop_next["topbar"] = list(desktop_config.get("topbar") or [])
        desktop_next["pageSchema"] = operations.coerce_dict(desktop_config.get("pageSchema") or {})
        desktop_next["pinnedWidgets"] = list(desktop_config.get("pinnedWidgets") or [])
        desktop_next["iconOrder"] = list(desktop_config.get("iconOrder") or [])
        desktop_next["widgetOrder"] = list(desktop_config.get("widgetOrder") or [])
        desktop_next["hiddenSections"] = list(desktop_config.get("hiddenSections") or [])

        webio_dict = operations.merge_webio_receivers(skill_decls)

        routing_dict = operations.coerce_dict((inputs.live_state or {}).get("routing") or {})
        routes = routing_dict.get("routes")
        routing_dict = {**routing_dict, "routes": operations.coerce_dict(routes)}

        resolved = operations.resolver_outputs_type(
            webspace_id=inputs.webspace_id,
            scenario_id=scenario_id,
            source_mode=source_mode,
            application=app_with_modals,
            catalog={
                "apps": [dict(it) for it in merged_apps],
                "widgets": [dict(it) for it in merged_widgets],
                "resources": operations.clone_json_like(merged_resources),
            },
            registry={
                "modals": list(merged_registry.get("modals") or []),
                "widgets": list(merged_registry.get("widgets") or []),
            },
            installed={
                "apps": list(installed_with_auto.get("apps") or []),
                "widgets": list(installed_with_auto.get("widgets") or []),
            },
            desktop=desktop_next,
            webio=webio_dict,
            routing=routing_dict,
            skill_decls=skill_decls,
        )
        return resolved


    def apply(
        self,
        runtime: Any,
        operations: WebspaceResolutionOperations,
        ydoc: Any,
        webspace_id: str,
        resolved: Any,
        *,
        inputs: Any | None = None,
        previous_resolved: Any | None = None,
        resolved_branch_fingerprints_override: Mapping[str, Any] | None = None,
        previous_branch_fingerprints_override: Mapping[str, Any] | None = None,
        expected_request_id: str | None = None,
        single_transaction: bool = False,
        materialization_status_per_phase: bool = True,
        force_selector_write: bool = False,
        verify_branch_fingerprints: bool = False,
    ) -> None:
        operations.raise_if_rebuild_request_superseded(webspace_id, expected_request_id)
        effective_inputs = inputs or operations.resolver_inputs_type(
            webspace_id=webspace_id,
            scenario_id=str(resolved.scenario_id or ""),
            source_mode=str(resolved.source_mode or ""),
        )
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        registry_map = ydoc.get_map("registry")
        runtime_map = ydoc.get_map("runtime")
        materialization_contract = operations.coerce_dict(effective_inputs.metadata.get("materialization") or {})
        if not materialization_contract:
            materialization_contract = operations.scenario_materialization_contract(
                resolved.scenario_id,
                source_mode=resolved.source_mode,
            )
        runtime_environment = dict(operations.runtime_environment_payload())
        runtime_environment["materialization"] = materialization_contract
        target_paths = operations.effective_branch_paths
        changed_paths: List[str] = []
        diff_applied_paths: List[str] = []
        patch_applied_paths: List[str] = []
        patch_actual_verified_paths: List[str] = []
        patch_fingerprint_mismatch_paths: List[str] = []
        patch_fallback_paths: List[str] = []
        patch_fallback_reasons: Dict[str, str] = {}
        replaced_paths: List[str] = []
        failed_paths: List[str] = []
        fingerprint_unchanged_paths: List[str] = []
        trusted_fingerprint_unchanged_paths: List[str] = []
        trusted_previous_fingerprint_patch_paths: List[str] = []
        stale_fingerprint_paths: List[str] = []
        defaults_failed = False
        selector_changed = False
        selector_reasserted = False
        selector_apply_mode = "not_attempted"
        phase_summaries: Dict[str, Dict[str, Any]] = {}
        phase_timings_ms: Dict[str, float] = {}
        branch_timings_ms: Dict[str, Dict[str, float]] = {}
        branch_apply_modes: Dict[str, str] = {}
        compatibility_presence = dict(effective_inputs.compatibility_cache_presence or {})
        resolved_branch_fingerprints = {
            str(key): str(value)
            for key, value in (resolved_branch_fingerprints_override or {}).items()
            if str(key).strip() and str(value or "").strip()
        }
        if not all(path in resolved_branch_fingerprints for path in operations.effective_branch_paths if path != "runtime.environment"):
            fallback_fingerprints = operations.resolved_output_branch_fingerprints(resolved)
            for path, fingerprint in fallback_fingerprints.items():
                resolved_branch_fingerprints.setdefault(path, fingerprint)
        resolved_branch_fingerprints["runtime.environment"] = operations.fingerprint_json_like(runtime_environment)
        previous_branch_values: Dict[str, Any] = {}
        previous_branch_fingerprints: Dict[str, str] = {}
        if previous_resolved is not None:
            previous_branch_values = {
                "ui.application": previous_resolved.application,
                "data.catalog": previous_resolved.catalog,
                "data.installed": previous_resolved.installed,
                "data.desktop": previous_resolved.desktop,
                "data.webio": previous_resolved.webio,
                "data.routing": previous_resolved.routing,
                "registry.merged": previous_resolved.registry,
            }
            previous_branch_fingerprints = {
                str(key): str(value)
                for key, value in (previous_branch_fingerprints_override or {}).items()
                if str(key).strip() and str(value or "").strip()
            }
            if not all(path in previous_branch_fingerprints for path in previous_branch_values):
                fallback_previous_fingerprints = operations.resolved_output_branch_fingerprints(previous_resolved)
                for path, fingerprint in fallback_previous_fingerprints.items():
                    previous_branch_fingerprints.setdefault(path, fingerprint)
        persisted_branch_fingerprints = operations.read_effective_branch_fingerprints(registry_map)
        effective_branch_fingerprints = dict(persisted_branch_fingerprints)
        pending_fingerprint_updates: Dict[str, str] = {}
        transaction_total = 0

        def _update_materialization_snapshot(phase_name: str) -> None:
            application = operations.coerce_dict(resolved.application or {})
            desktop = operations.coerce_dict(application.get("desktop") or {})
            modals = operations.coerce_dict(application.get("modals") or {})
            page_schema = operations.coerce_dict(desktop.get("pageSchema") or {})
            topbar = desktop.get("topbar") if isinstance(desktop.get("topbar"), list) else []
            page_widgets = page_schema.get("widgets") if isinstance(page_schema.get("widgets"), list) else []
            installed = operations.coerce_dict(resolved.installed or {})
            include_catalog = phase_name != "structure"
            snapshot = operations.build_materialization_snapshot(
                webspace_id=webspace_id,
                current_scenario=resolved.scenario_id,
                has_ui_application=bool(application),
                has_desktop_config=bool(desktop),
                has_desktop_page_schema=bool(page_schema),
                has_apps_catalog_modal="apps_catalog" in modals,
                has_widgets_catalog_modal="widgets_catalog" in modals,
                has_catalog_apps=include_catalog and isinstance(resolved.catalog.get("apps"), list),
                has_catalog_widgets=include_catalog and isinstance(resolved.catalog.get("widgets"), list),
                has_data_desktop=include_catalog and isinstance(resolved.desktop, Mapping),
                has_installed_apps=include_catalog and isinstance(installed.get("apps"), list),
                has_installed_widgets=include_catalog and isinstance(installed.get("widgets"), list),
                has_scenario_ui_application=bool(compatibility_presence.get("scenario_ui_application")),
                has_scenario_registry_entry=bool(compatibility_presence.get("scenario_registry_entry")),
                has_scenario_catalog=bool(compatibility_presence.get("scenario_catalog")),
                has_data_webio=include_catalog and isinstance(resolved.webio, Mapping),
                has_data_routing=include_catalog and isinstance(resolved.routing, Mapping),
                has_registry_merged=bool(resolved.registry),
                catalog_apps_count=len(resolved.catalog.get("apps") or []) if include_catalog else 0,
                catalog_widgets_count=len(resolved.catalog.get("widgets") or []) if include_catalog else 0,
                installed_apps_count=len(installed.get("apps") or []) if include_catalog else 0,
                installed_widgets_count=len(installed.get("widgets") or []) if include_catalog else 0,
                topbar_count=len(topbar),
                page_widget_count=len(page_widgets),
                rebuild_state=operations.describe_webspace_rebuild_state(webspace_id),
                required_branches=operations.normalize_materialization_required_branches(materialization_contract)
                or list(operations.default_materialization_required_branches),
                snapshot_source=f"semantic_rebuild:{phase_name}",
                stale=False,
            )
            current_request_id = str(operations.describe_webspace_rebuild_state(webspace_id).get("request_id") or "").strip() or None
            operations.set_webspace_rebuild_status_if_current(
                webspace_id,
                current_request_id,
                materialization=snapshot,
            )

        def _apply_branch(
            txn: Any,
            path: str,
            y_map: Any,
            key: str,
            value: Any,
            *,
            fingerprint_updates: Dict[str, str],
            ignore_errors: bool = False,
        ) -> None:
            branch_started = time.perf_counter()
            branch_timing = branch_timings_ms.setdefault(path, {})
            fingerprint = ""
            changed = False
            apply_mode = "unknown"
            stale_branch = False
            try:
                stage_started = time.perf_counter()
                fingerprint = str(resolved_branch_fingerprints.get(path) or "").strip()
                branch_timing["fingerprint_lookup"] = operations.elapsed_ms(stage_started)
                actual_branch_fingerprint: str | None = None
                if (
                    fingerprint
                    and str(effective_branch_fingerprints.get(path) or "").strip() == fingerprint
                ):
                    stage_started = time.perf_counter()
                    trusted_previous_fingerprint = str(previous_branch_fingerprints.get(path) or "").strip()
                    if (
                        trusted_previous_fingerprint == fingerprint
                        and not verify_branch_fingerprints
                        and operations.trust_previous_materialized_branch_fingerprints_enabled()
                    ):
                        has_value = operations.has_effective_branch_value(y_map, key)
                        branch_timing["presence_check"] = operations.elapsed_ms(stage_started)
                        if has_value:
                            fingerprint_unchanged_paths.append(path)
                            trusted_fingerprint_unchanged_paths.append(path)
                            fingerprint_updates[path] = fingerprint
                            pending_fingerprint_updates[path] = fingerprint
                            branch_apply_modes[path] = "trusted_previous_fingerprint_unchanged"
                            return
                    else:
                        try:
                            actual_branch_fingerprint = operations.fingerprint_json_like(y_map.get(key))
                        except Exception:
                            actual_branch_fingerprint = ""
                        branch_timing["actual_fingerprint"] = operations.elapsed_ms(stage_started)
                        if actual_branch_fingerprint == fingerprint:
                            fingerprint_unchanged_paths.append(path)
                            fingerprint_updates[path] = fingerprint
                            pending_fingerprint_updates[path] = fingerprint
                            branch_apply_modes[path] = "fingerprint_unchanged"
                            return
                    if path not in stale_fingerprint_paths:
                        stale_fingerprint_paths.append(path)
                    stale_branch = True

                # Continue into previous-payload patching when the stored
                # fingerprint was trusted but the branch is missing, or when
                # the verified live branch did not match the stored token.
                stage_started = time.perf_counter()
                previous_fingerprint = str(previous_branch_fingerprints.get(path) or "").strip()
                previous_fingerprint_matches = False
                if previous_fingerprint and path in previous_branch_values and path not in operations.whole_branch_replace_paths:
                    verify_started = time.perf_counter()
                    trusted_previous_state = (
                        not verify_branch_fingerprints
                        and operations.trust_previous_materialized_branch_fingerprints_enabled()
                        and str(effective_branch_fingerprints.get(path) or "").strip() == previous_fingerprint
                    )
                    if trusted_previous_state:
                        previous_fingerprint_matches = True
                        trusted_previous_fingerprint_patch_paths.append(path)
                        branch_timing["previous_fingerprint_trusted"] = operations.elapsed_ms(verify_started)
                    elif actual_branch_fingerprint is None:
                        try:
                            actual_branch_fingerprint = operations.fingerprint_json_like(y_map.get(key))
                        except Exception:
                            actual_branch_fingerprint = ""
                        branch_timing["previous_actual_fingerprint"] = operations.elapsed_ms(verify_started)
                    else:
                        branch_timing["previous_actual_fingerprint_reused"] = operations.elapsed_ms(verify_started)
                    if trusted_previous_state or actual_branch_fingerprint == previous_fingerprint:
                        previous_fingerprint_matches = True
                        patch_actual_verified_paths.append(path)
                    else:
                        patch_fingerprint_mismatch_paths.append(path)
                branch_timing["previous_check"] = operations.elapsed_ms(stage_started)

                stage_started = time.perf_counter()
                try:
                    if stale_branch:
                        changed, apply_mode = operations.replace_map_value(y_map, txn, key, value)
                    elif (
                        path in previous_branch_values
                        and previous_fingerprint
                        and previous_fingerprint_matches
                        and path not in operations.whole_branch_replace_paths
                    ):
                        try:
                            current_for_patch = y_map.get(key)
                        except Exception:
                            current_for_patch = None
                        if not operations.is_y_map_value(current_for_patch):
                            patch_fallback_paths.append(path)
                            patch_fallback_reasons[path] = f"current_not_y_map:{type(current_for_patch).__name__}"
                            changed, apply_mode = operations.set_map_value_if_changed(y_map, txn, key, value)
                        elif operations.mapping_items(value) is None or operations.mapping_items(previous_branch_values[path]) is None:
                            patch_fallback_paths.append(path)
                            patch_fallback_reasons[path] = "non_mapping_payload"
                            changed, apply_mode = operations.set_map_value_if_changed(y_map, txn, key, value)
                        else:
                            changed, apply_mode = operations.patch_map_value_from_previous(
                                y_map,
                                txn,
                                key,
                                value,
                                previous_branch_values[path],
                            )
                    elif path in operations.whole_branch_replace_paths:
                        changed, apply_mode = operations.replace_map_value(y_map, txn, key, value)
                    else:
                        changed, apply_mode = operations.set_map_value_if_changed(y_map, txn, key, value)
                finally:
                    branch_timing["apply"] = operations.elapsed_ms(stage_started)
            except Exception:
                branch_apply_modes[path] = "failed"
                if not ignore_errors:
                    raise
                failed_paths.append(path)
                return
            finally:
                branch_timing["total"] = operations.elapsed_ms(branch_started)
            if fingerprint:
                effective_branch_fingerprints[path] = fingerprint
                fingerprint_updates[path] = fingerprint
                pending_fingerprint_updates[path] = fingerprint
            branch_apply_modes[path] = f"{'changed' if changed else 'unchanged'}:{apply_mode}"
            if changed:
                changed_paths.append(path)
                if apply_mode == "diff":
                    diff_applied_paths.append(path)
                elif apply_mode == "patch":
                    patch_applied_paths.append(path)
                else:
                    replaced_paths.append(path)

        def _apply_phase(
            name: str,
            branch_specs: tuple[tuple[str, Any, str, Any, bool], ...],
            *,
            apply_defaults: bool = False,
            flush_fingerprints: bool = False,
            shared_txn: Any | None = None,
        ) -> None:
            nonlocal defaults_failed
            nonlocal transaction_total
            nonlocal selector_changed
            nonlocal selector_reasserted
            nonlocal selector_apply_mode
            operations.raise_if_rebuild_request_superseded(webspace_id, expected_request_id)
            phase_started = time.perf_counter()
            phase_changed_before = len(changed_paths)
            phase_diff_before = len(diff_applied_paths)
            phase_patch_before = len(patch_applied_paths)
            phase_replaced_before = len(replaced_paths)
            phase_failed_before = len(failed_paths)
            phase_fingerprint_unchanged_before = len(fingerprint_unchanged_paths)
            phase_trusted_fingerprint_unchanged_before = len(trusted_fingerprint_unchanged_paths)
            phase_stale_fingerprint_before = len(stale_fingerprint_paths)
            phase_defaults_failed = False

            def _apply_phase_body(txn: Any) -> None:
                nonlocal defaults_failed
                nonlocal selector_changed
                nonlocal selector_reasserted
                nonlocal selector_apply_mode
                phase_fingerprint_updates: Dict[str, str] = {}
                if apply_defaults:
                    try:
                        runtime._apply_ydoc_defaults_in_txn(ydoc, txn, resolved.skill_decls)
                    except Exception:
                        defaults_failed = True
                        phase_defaults_failed = True
                        operations.logger.warning("failed to apply ydoc_defaults for webspace=%s", webspace_id, exc_info=True)

                if name == "structure":
                    selector_target = str(resolved.scenario_id or "").strip()
                    if selector_target:
                        if force_selector_write:
                            selector_changed = ui_map.get("current_scenario") != selector_target
                            ui_map.set(txn, "current_scenario", selector_target)
                            selector_reasserted = True
                            selector_apply_mode = "reasserted"
                        else:
                            selector_changed, selector_apply_mode = operations.set_map_value_if_changed(
                                ui_map,
                                txn,
                                "current_scenario",
                                selector_target,
                            )

                for path, y_map, key, value, ignore_errors in branch_specs:
                    _apply_branch(
                        txn,
                        path,
                        y_map,
                        key,
                        value,
                        fingerprint_updates=phase_fingerprint_updates,
                        ignore_errors=ignore_errors,
                    )
                if flush_fingerprints and pending_fingerprint_updates:
                    operations.write_effective_branch_fingerprints(
                        registry_map,
                        txn,
                        current=effective_branch_fingerprints,
                        updates=pending_fingerprint_updates,
                    )

            if shared_txn is None:
                with ydoc.begin_transaction() as txn:
                    transaction_total += 1
                    _apply_phase_body(txn)
            else:
                _apply_phase_body(shared_txn)

            phase_changed_paths = list(changed_paths[phase_changed_before:])
            phase_diff_paths = list(diff_applied_paths[phase_diff_before:])
            phase_patch_paths = list(patch_applied_paths[phase_patch_before:])
            phase_replaced_paths = list(replaced_paths[phase_replaced_before:])
            phase_failed_paths = list(failed_paths[phase_failed_before:])
            phase_fingerprint_unchanged_paths = list(fingerprint_unchanged_paths[phase_fingerprint_unchanged_before:])
            phase_trusted_fingerprint_unchanged_paths = list(
                trusted_fingerprint_unchanged_paths[phase_trusted_fingerprint_unchanged_before:]
            )
            phase_stale_fingerprint_paths = list(stale_fingerprint_paths[phase_stale_fingerprint_before:])
            phase_paths = [path for path, _y_map, _key, _value, _ignore_errors in branch_specs]
            phase_branch_timings = {
                path: dict(branch_timings_ms.get(path) or {})
                for path in phase_paths
                if branch_timings_ms.get(path)
            }
            phase_branch_modes = {
                path: str(branch_apply_modes.get(path) or "")
                for path in phase_paths
                if str(branch_apply_modes.get(path) or "")
            }
            branch_count = len(branch_specs)
            phase_summary: Dict[str, Any] = {
                "branch_count": branch_count,
                "changed_branches": len(phase_changed_paths),
                "unchanged_branches": branch_count - len(phase_changed_paths) - len(phase_failed_paths),
                "failed_branches": len(phase_failed_paths),
                "changed_paths": phase_changed_paths,
            }
            if phase_diff_paths:
                phase_summary["diff_applied_branches"] = len(phase_diff_paths)
                phase_summary["diff_applied_paths"] = phase_diff_paths
            if phase_patch_paths:
                phase_summary["patch_applied_branches"] = len(phase_patch_paths)
                phase_summary["patch_applied_paths"] = phase_patch_paths
            if phase_replaced_paths:
                phase_summary["replaced_branches"] = len(phase_replaced_paths)
                phase_summary["replaced_paths"] = phase_replaced_paths
            if phase_fingerprint_unchanged_paths:
                phase_summary["fingerprint_unchanged_branches"] = len(phase_fingerprint_unchanged_paths)
                phase_summary["fingerprint_unchanged_paths"] = phase_fingerprint_unchanged_paths
            if phase_trusted_fingerprint_unchanged_paths:
                phase_summary["trusted_fingerprint_unchanged_branches"] = len(
                    phase_trusted_fingerprint_unchanged_paths
                )
                phase_summary["trusted_fingerprint_unchanged_paths"] = phase_trusted_fingerprint_unchanged_paths
            if phase_stale_fingerprint_paths:
                phase_summary["stale_fingerprint_branches"] = len(phase_stale_fingerprint_paths)
                phase_summary["stale_fingerprint_paths"] = phase_stale_fingerprint_paths
            if phase_failed_paths:
                phase_summary["failed_paths"] = phase_failed_paths
            if phase_defaults_failed:
                phase_summary["defaults_failed"] = True
            if phase_branch_timings:
                phase_summary["branch_timings_ms"] = phase_branch_timings
            if phase_branch_modes:
                phase_summary["branch_apply_modes"] = phase_branch_modes
            phase_summaries[name] = phase_summary
            phase_timings_ms[f"apply_{name}"] = operations.elapsed_ms(phase_started)
            if materialization_status_per_phase:
                _update_materialization_snapshot(name)

        structure_specs = (
            ("ui.application", ui_map, "application", resolved.application, False),
            ("registry.merged", registry_map, "merged", resolved.registry, False),
            ("runtime.environment", runtime_map, "environment", runtime_environment, False),
        )
        interactive_specs = (
            ("data.catalog", data_map, "catalog", resolved.catalog, False),
            ("data.installed", data_map, "installed", resolved.installed, False),
            ("data.desktop", data_map, "desktop", resolved.desktop, True),
            ("data.webio", data_map, "webio", resolved.webio, True),
            ("data.routing", data_map, "routing", resolved.routing, True),
        )
        if single_transaction:
            combined_started = time.perf_counter()
            with ydoc.begin_transaction() as txn:
                transaction_total += 1
                _apply_phase(
                    "structure",
                    structure_specs,
                    apply_defaults=True,
                    flush_fingerprints=False,
                    shared_txn=txn,
                )
                _apply_phase(
                    "interactive",
                    interactive_specs,
                    flush_fingerprints=True,
                    shared_txn=txn,
                )
            phase_timings_ms["apply_combined_transaction"] = operations.elapsed_ms(combined_started)
        else:
            _apply_phase(
                "structure",
                structure_specs,
                apply_defaults=True,
                flush_fingerprints=False,
            )
            _apply_phase(
                "interactive",
                interactive_specs,
                flush_fingerprints=True,
            )
        if not materialization_status_per_phase:
            _update_materialization_snapshot("interactive")

        runtime._last_apply_summary = {
            "branch_count": len(target_paths),
            "changed_branches": len(changed_paths),
            "unchanged_branches": len(target_paths) - len(changed_paths) - len(failed_paths),
            "failed_branches": len(failed_paths),
            "changed_paths": list(changed_paths),
            "defaults_failed": defaults_failed,
            "transaction_total": transaction_total,
            "phases": phase_summaries,
            "branch_timings_ms": {path: dict(values) for path, values in branch_timings_ms.items()},
            "branch_apply_modes": dict(branch_apply_modes),
            "selector_changed": bool(selector_changed),
            "selector_reasserted": bool(selector_reasserted),
            "selector_apply_mode": selector_apply_mode,
            "verified_branch_fingerprints": bool(verify_branch_fingerprints),
        }
        if diff_applied_paths:
            runtime._last_apply_summary["diff_applied_branches"] = len(diff_applied_paths)
            runtime._last_apply_summary["diff_applied_paths"] = list(diff_applied_paths)
        if patch_applied_paths:
            runtime._last_apply_summary["patch_applied_branches"] = len(patch_applied_paths)
            runtime._last_apply_summary["patch_applied_paths"] = list(patch_applied_paths)
        if patch_actual_verified_paths:
            runtime._last_apply_summary["patch_actual_verified_branches"] = len(patch_actual_verified_paths)
            runtime._last_apply_summary["patch_actual_verified_paths"] = list(patch_actual_verified_paths)
        if patch_fingerprint_mismatch_paths:
            runtime._last_apply_summary["patch_fingerprint_mismatch_branches"] = len(patch_fingerprint_mismatch_paths)
            runtime._last_apply_summary["patch_fingerprint_mismatch_paths"] = list(patch_fingerprint_mismatch_paths)
        if patch_fallback_paths:
            runtime._last_apply_summary["patch_fallback_branches"] = len(patch_fallback_paths)
            runtime._last_apply_summary["patch_fallback_paths"] = list(patch_fallback_paths)
            runtime._last_apply_summary["patch_fallback_reasons"] = dict(patch_fallback_reasons)
        if replaced_paths:
            runtime._last_apply_summary["replaced_branches"] = len(replaced_paths)
            runtime._last_apply_summary["replaced_paths"] = list(replaced_paths)
        if fingerprint_unchanged_paths:
            runtime._last_apply_summary["fingerprint_unchanged_branches"] = len(fingerprint_unchanged_paths)
            runtime._last_apply_summary["fingerprint_unchanged_paths"] = list(fingerprint_unchanged_paths)
        if trusted_fingerprint_unchanged_paths:
            runtime._last_apply_summary["trusted_fingerprint_unchanged_branches"] = len(
                trusted_fingerprint_unchanged_paths
            )
            runtime._last_apply_summary["trusted_fingerprint_unchanged_paths"] = list(trusted_fingerprint_unchanged_paths)
        if trusted_previous_fingerprint_patch_paths:
            runtime._last_apply_summary["trusted_previous_fingerprint_patch_branches"] = len(
                trusted_previous_fingerprint_patch_paths
            )
            runtime._last_apply_summary["trusted_previous_fingerprint_patch_paths"] = list(
                trusted_previous_fingerprint_patch_paths
            )
        if stale_fingerprint_paths:
            runtime._last_apply_summary["stale_fingerprint_branches"] = len(stale_fingerprint_paths)
            runtime._last_apply_summary["stale_fingerprint_paths"] = list(stale_fingerprint_paths)
        if failed_paths:
            runtime._last_apply_summary["failed_paths"] = list(failed_paths)
        runtime._last_apply_phase_timings_ms = phase_timings_ms or None

