from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator

from .contracts import (
    SiteStudioContractError,
    digest,
    normalize_site_source,
    validate_contract,
)


RENDERER_ABI = "adaos.public-renderer.v1"
THEME_CONTRACT = "adaos.site-theme.v1"
PACKAGE_VERSION = "1.0.0"
PACKAGE_QUALIFIED_AT = "2026-11-03T00:00:00+00:00"
THEME_TOKENS = {
    "color.canvas": "--ada-site-canvas",
    "color.surface": "--ada-site-surface",
    "color.ink": "--ada-site-ink",
    "color.muted": "--ada-site-muted",
    "color.accent": "--ada-site-accent",
    "color.accentSoft": "--ada-site-accent-soft",
    "color.signal": "--ada-site-signal",
    "color.dark": "--ada-site-dark",
    "color.darkInk": "--ada-site-dark-ink",
    "color.line": "--ada-site-line",
    "size.maxWidth": "--ada-site-max-width",
}
FORBIDDEN_KEYS = {
    "backend",
    "css",
    "html",
    "javascript",
    "mcp",
    "resourcequery",
    "script",
    "srcdoc",
    "stream",
    "y",
    "yjs",
}


def default_component_catalog() -> dict[str, Any]:
    variants = {
        "site.hero": ["default", "media", "emblem"],
        "site.section": ["default", "manifesto"],
        "site.cardGrid": [
            "default",
            "applications",
            "people",
            "compare",
            "faq",
            "pricing",
            "testimonials",
            "environmentGraph",
        ],
        "site.flow": ["default", "lifecycle", "timeline"],
        "site.callToAction": ["default", "resources"],
    }
    specs = [
        (
            "site.header",
            "site.core",
            "site.navigation",
            ["brand", "items"],
            [("brand.name", "text"), ("brand.logo.src", "asset"), ("items", "list")],
        ),
        (
            "site.hero",
            "site.core",
            "site.hero",
            ["title", "statement", "body", "actions"],
            [
                ("title", "text"),
                ("statement", "text"),
                ("body", "textarea"),
                ("actions", "list"),
            ],
        ),
        (
            "site.section",
            "site.core",
            "site.narrative",
            ["title", "paragraphs"],
            [
                ("title", "text"),
                ("paragraphs", "list"),
                ("media.src", "asset"),
                ("media.alt", "text"),
            ],
        ),
        (
            "site.cardGrid",
            "site.collections",
            "site.card_collection",
            ["title", "items"],
            [
                ("title", "text"),
                ("items", "list"),
                ("columns", "number"),
                ("media.src", "asset"),
                ("media.alt", "text"),
            ],
        ),
        (
            "site.flow",
            "site.collections",
            "site.flow",
            ["title", "steps"],
            [("title", "text"), ("steps", "list")],
        ),
        (
            "site.media",
            "site.media",
            "site.media_story",
            ["title", "body", "media"],
            [
                ("title", "text"),
                ("body", "textarea"),
                ("media.src", "asset"),
                ("media.alt", "text"),
            ],
        ),
        (
            "site.callToAction",
            "site.core",
            "site.call_to_action",
            ["title", "body", "actions"],
            [("title", "text"), ("body", "textarea"), ("actions", "list")],
        ),
        (
            "site.footer",
            "site.core",
            "site.footer",
            ["brand", "items", "legal"],
            [("brand", "text"), ("items", "list"), ("legal", "textarea")],
        ),
        (
            "site.dashboard", "site.analytics", "site.dashboard",
            ["title", "rows", "idField", "labelField", "searchFields", "columns"],
            [("title", "text"), ("intro", "textarea"), ("rows", "list"),
             ("columns", "list"), ("filters", "list"), ("metrics", "list"),
             ("distributions", "list"), ("detailColumns", "list"),
             ("layers", "list"), ("map.publicKey", "text"), ("note", "textarea")],
        ),
        (
            "site.map", "site.analytics", "site.map",
            ["rows", "map", "labelField"],
            [("rows", "list"), ("labelField", "text"), ("map.publicKey", "text"),
             ("map.latitudeField", "text"), ("map.longitudeField", "text")],
        ),
        (
            "site.divider", "site.decoration", "site.divider", ["title"],
            [("title", "text"), ("media.src", "asset"), ("tone", "text")],
        ),
    ]
    components = []
    for component_id, family, resource_type, required, fields in specs:
        optional = (
            ["media"]
            if component_id in {"site.section", "site.cardGrid"}
            else []
        )
        properties = {field.split(".")[0]: {} for field in required}
        properties.update({field.split(".")[0]: {} for field in optional})
        if component_id in {"site.dashboard", "site.map"}:
            properties.update(_analytics_properties())
        components.append(
            {
                "id": component_id,
                "family": family,
                "surface_profiles": ["site"],
                "resource_types": [resource_type],
                "required_fields": required,
                "optional_fields": optional,
                "data_schema": {
                    "type": "object",
                    "required": required,
                    "properties": properties,
                    "additionalProperties": True,
                },
                "defaults": _component_defaults(component_id),
                "editor": {
                    "label": component_id.removeprefix("site."),
                    "addable": component_id not in {"site.header", "site.footer"},
                    "fields": ([{
                        "path": "variant",
                        "control": "select",
                        "label": "variant",
                        "required": False,
                        "options": variants[component_id],
                    }] if component_id in variants else []) + [
                        {
                            "path": path,
                            "control": control,
                            "label": "Браузерный ключ Яндекс Карт (доступен посетителям)"
                            if path == "map.publicKey" else path,
                            "required": path in required,
                        }
                        for path, control in fields
                    ],
                },
                "representative_states": [
                    "default", "long_content", "compact", "empty", "error", "loading",
                ],
                "accessibility": "Semantic landmark or labelled section with keyboard-operable links.",
                "seo": "Visible semantic resource content participates in the route representation.",
                "theme_slots": list(THEME_TOKENS),
                "source_map": "widget, semantic resource, route section, and theme anchors",
                "migration": {"contract_version": "v1", "status": "supported"},
            }
        )
    return {
        "schema": "adaos.site_component_catalog.v1",
        "renderer_abi": RENDERER_ABI,
        "components": components,
    }


def _component_defaults(component_id: str) -> dict[str, Any]:
    if component_id == "site.cardGrid":
        return {"columns": 3}
    if component_id in {"site.dashboard", "site.map"}:
        result: dict[str, Any] = {
            "rows": [], "labelField": "name",
            "map": {"publicKey": "", "latitudeField": "latitude", "longitudeField": "longitude"},
        }
        if component_id == "site.dashboard":
            result.update({"title": "Дашборд", "idField": "id", "searchFields": ["name"],
                           "columns": [{"field": "name", "label": "Название"}],
                           "filters": [], "metrics": [{"label": "объектов", "operation": "count"}]})
        return result
    if component_id == "site.divider":
        return {"title": "Раздел", "tone": "dark"}
    return {}


def _analytics_properties() -> dict[str, Any]:
    field = {"type": "string", "minLength": 1, "maxLength": 128}
    column = {
        "type": "object", "required": ["field", "label"],
        "properties": {"field": field, "label": field,
                       "scale": {"type": "number", "exclusiveMinimum": 0},
                       "decimals": {"type": "integer", "minimum": 0, "maximum": 8}},
        "additionalProperties": False,
    }
    return {
        "rows": {"type": "array", "maxItems": 10000, "items": {"type": "object"}},
        "idField": field, "labelField": field, "categoryField": field,
        "searchFields": {"type": "array", "items": field, "maxItems": 20},
        "columns": {"type": "array", "minItems": 1, "maxItems": 50, "items": column},
        "detailColumns": {"type": "array", "maxItems": 100, "items": column},
        "exportColumns": {"type": "array", "maxItems": 100, "items": column},
        "colors": {"type": "object", "additionalProperties": {"type": "string", "pattern": "^#[a-fA-F0-9]{3,8}$"}},
        "metrics": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "required": ["label", "operation"],
            "properties": {"label": field, "field": field,
                           "operation": {"enum": ["count", "sum", "countTruthy"]},
                           "scale": {"type": "number", "exclusiveMinimum": 0},
                           "decimals": {"type": "integer", "minimum": 0, "maximum": 8}},
            "additionalProperties": False,
        }},
        "layers": {"type": "array", "maxItems": 20, "items": {
            "type": "object", "required": ["id", "label", "field"],
            "properties": {"id": field, "label": field, "field": field}, "additionalProperties": False,
        }},
        "distributions": {"type": "array", "maxItems": 12, "items": {
            "type": "object", "required": ["label", "field"],
            "properties": {"label": field, "field": field}, "additionalProperties": False,
        }},
        "map": {
            "type": "object", "required": ["publicKey", "latitudeField", "longitudeField"],
            "properties": {"publicKey": {"type": "string", "maxLength": 100},
                           "latitudeField": field, "longitudeField": field, "valueField": field,
                           "center": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
                           "zoom": {"type": "number", "minimum": 1, "maximum": 18}},
            "additionalProperties": False,
        },
        "filters": {"type": "array", "maxItems": 30, "items": {
            "type": "object", "required": ["id", "label", "field", "kind"],
            "properties": {"id": field, "label": field, "field": field,
                           "kind": {"enum": ["select", "multi", "range", "boolean"]},
                           "options": {"type": "array", "items": {"type": "string"}},
                           "scale": {"type": "number", "exclusiveMinimum": 0},
                           "min": {"type": "number"}, "max": {"type": "number"},
                           "whenField": field, "whenValue": {"type": "string"},
                           "anyFields": {"type": "array", "items": field}},
            "additionalProperties": False,
        }},
    }


def compile_site_revision(
    source: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any] | None = None,
    prompt_packet_digest: str | None = None,
    asset_resolver: Callable[[Mapping[str, Any]], bytes] | None = None,
    channel: str = "dev2027",
) -> dict[str, Any]:
    source = normalize_site_source(source)
    catalog = deepcopy(dict(catalog or default_component_catalog()))
    validate_contract("site.v1.schema.json", source)
    validate_contract("site_component_catalog.v1.schema.json", catalog)
    if catalog["renderer_abi"] != RENDERER_ABI:
        raise SiteStudioContractError("component catalog renderer ABI mismatch")
    _reject_executable(source)
    source_digest = digest(source)
    prompt_digest = prompt_packet_digest or digest({})
    components = {component["id"]: component for component in catalog["components"]}
    resources = {resource["id"]: resource for resource in source["resources"]}
    if len(resources) != len(source["resources"]):
        raise SiteStudioContractError("duplicate semantic resource id")
    asset_paths = {asset["id"]: asset["public_path"] for asset in source["assets"]}
    render_resources = deepcopy(resources)
    for resource in render_resources.values():
        resource["data"] = _resolve_asset_references(resource["data"], asset_paths)

    artifact_map: dict[str, Any] = {}
    resource_entries = []
    for resource in render_resources.values():
        if not resource["public"]:
            continue
        ref = f"resources/{resource['id']}.json"
        envelope = {
            "schema": "adaos.site_resource.v1",
            "site_id": source["id"],
            "resource_id": resource["id"],
            "resource_type": resource["type"],
            "locale": source["locale"],
            "source_digest": source_digest,
            "data": deepcopy(resource["data"]),
        }
        artifact_map[ref] = envelope
        resource_entries.append(
            {
                "id": resource["id"],
                "type": resource["type"],
                "ref": ref,
                "digest": digest(envelope),
            }
        )

    route_entries = []
    components_used: set[str] = set()
    for route in source["routes"]:
        compiled = _compile_route(
            source, route, render_resources, components, source_digest
        )
        page_ref = f"pages/{route['id']}.webui.json"
        route_ref = f"routes/{route['id']}.route.json"
        source_map_ref = f"source-maps/{route['id']}.source-map.json"
        artifact_map[page_ref] = compiled["page"]
        artifact_map[route_ref] = compiled["route"]
        artifact_map[source_map_ref] = compiled["source_map"]
        artifact_map[f"routes/{route['id']}.md"] = _route_markdown(
            compiled["route"]
        )
        components_used.update(widget["type"] for widget in compiled["page"]["widgets"])
        route_entries.append(
            {
                "id": route["id"],
                "path": route["path"],
                "artifact_ref": route_ref,
                "artifact_digest": digest(compiled["route"]),
                "page_graph_ref": page_ref,
                "page_graph_digest": digest(compiled["page"]),
                "source_map_ref": source_map_ref,
                "source_map_digest": digest(compiled["source_map"]),
            }
        )

    assets = _compile_assets(source["assets"], asset_resolver)
    theme = _compile_theme(source["theme"]["tokens"])
    projection_body = {
        "schema": "adaos.site_projection.v1",
        "site_id": source["id"],
        "revision_id": source["revision"]["id"],
        "locale": source["locale"],
        "compiled_at": source["revision"]["created_at"],
        "source_digest": source_digest,
        "prompt_packet_digest": prompt_digest,
        "routes": route_entries,
        "resources": resource_entries,
        "assets": assets,
        "theme": theme,
        "discovery_ref": ".well-known/adaos-site.json",
        "policy": {
            "data_sources": ["static"],
            "denied_data_sources": ["api", "y", "mcp", "resourceQuery", "stream"],
            "arbitrary_html": False,
            "arbitrary_css": False,
            "arbitrary_javascript": False,
        },
    }
    projection = {**projection_body, "projection_digest": digest(projection_body)}
    validate_contract("site_projection.v1.schema.json", projection)
    packages = _compile_packages(catalog)
    release_set = _compile_release_set(catalog, packages, channel)
    used_families = {
        components[component_id]["family"] for component_id in components_used
    }
    deployment_body = {
        "schema": "adaos.public_site_deployment.v1",
        "deployment_id": f"{source['id']}:{source['revision']['id']}",
        "site_id": source["id"],
        "release_id": source["revision"]["id"],
        "projection_digest": projection["projection_digest"],
        "component_release_set": {
            "id": release_set["set_id"],
            "digest": release_set["set_digest"],
        },
        "required_packages": [
            {"family": entry["family"], "digest": entry["digest"]}
            for entry in release_set["packages"]
            if entry["family"] in used_families
        ],
        "renderer_abi": RENDERER_ABI,
        "theme_contract": THEME_CONTRACT,
        "theme_digest": digest(theme),
        # The deployment locks physical objects, while projection assets retain
        # their distinct semantic identities and public paths.
        "asset_digests": sorted({asset["digest"] for asset in assets}),
        "acceptance": {
            "kind": "owner",
            "receipt_id": f"draft:{source['revision']['id']}",
            "accepted_at": source["revision"]["created_at"],
        },
        "created_at": source["revision"]["created_at"],
        "delivery": {
            "artifact_layout": "adaos.public-site.bundle.v1",
            "projection_ref": "site.projection.json",
            "component_release_set_ref": "components/release-set.json",
        },
    }
    deployment = {**deployment_body, "deployment_digest": digest(deployment_body)}
    validate_contract("public_site_deployment.v1.schema.json", deployment)
    discovery = {
        "schema": "adaos.site_discovery.v1",
        "site_id": source["id"],
        "name": source["identity"]["name"],
        "canonical_origin": source["identity"]["canonical_origin"],
        "locale": source["locale"],
        "source_digest": source_digest,
        "projection_digest": projection["projection_digest"],
        "projection_ref": "../site.projection.json",
        "routes": [
            {
                "id": route["id"],
                "path": route["path"],
                "artifact_ref": f"../{route['artifact_ref']}",
                "markdown_ref": f"../routes/{route['id']}.md",
            }
            for route in route_entries
        ],
        "resources": [
            {
                "id": resource["id"],
                "type": resource["type"],
                "ref": f"../{resource['ref']}",
            }
            for resource in resource_entries
        ],
        "sitemap_ref": "../sitemap.xml",
        "llms_ref": "../llms.txt",
    }
    validate_contract("site_discovery.v1.schema.json", discovery)
    artifact_map.update(
        {
            "site.projection.json": projection,
            "site.deployment.json": deployment,
            "components/release-set.json": release_set,
            ".well-known/adaos-site.json": discovery,
            "sitemap.xml": _sitemap(source["routes"]),
            "robots.txt": _robots(source["identity"]["canonical_origin"]),
            "llms.txt": _llms_txt(discovery, artifact_map),
        }
    )
    artifact_map.update(
        {entry["artifact_ref"]: entry["artifact"] for entry in packages}
    )
    required_package_digests = {
        entry["digest"] for entry in deployment["required_packages"]
    }
    preview_route = route_entries[0]
    preview = {
        "schema": "adaos.site_preview.v1",
        "source": source,
        "deployment": deployment,
        "component_release_set": release_set,
        "component_packages": [
            entry["artifact"]
            for entry in packages
            if entry["artifact"]["digest"] in required_package_digests
        ],
        "projection": projection,
        "route": artifact_map[preview_route["artifact_ref"]],
        "page": artifact_map[preview_route["page_graph_ref"]],
        "source_map": artifact_map[preview_route["source_map_ref"]],
        "diagnostics": {
            "route_map": [
                {"id": route["id"], "path": route["path"]} for route in route_entries
            ],
            "source_materials": [],
            "assumptions": [],
            "questions": [],
            "changed_resources": [resource["id"] for resource in resource_entries],
            "source_digest": source_digest,
            "projection_digest": projection["projection_digest"],
            "deployment_digest": deployment["deployment_digest"],
            "component_set_digest": release_set["set_digest"],
            "renderer_abi": RENDERER_ABI,
            "unsupported_capabilities": [],
        },
    }
    return {
        "source": source,
        "source_digest": source_digest,
        "projection": projection,
        "projection_digest": projection["projection_digest"],
        "deployment": deployment,
        "deployment_digest": deployment["deployment_digest"],
        "component_release_set": release_set,
        "component_set_digest": release_set["set_digest"],
        "component_packages": [entry["artifact"] for entry in packages],
        "artifacts": artifact_map,
        "preview": preview,
        "artifact_digest": digest(artifact_map),
    }


def _compile_route(source, route, resources, components, source_digest):
    widgets = []
    entries = []
    public_refs = []
    seen_sections: set[str] = set()
    for section in route["sections"]:
        if section["id"] in seen_sections:
            raise SiteStudioContractError(f"duplicate section id: {section['id']}")
        seen_sections.add(section["id"])
        component = components.get(section["component"])
        resource = resources.get(section["resource_ref"])
        if not component or not resource or not resource["public"]:
            raise SiteStudioContractError(f"unresolved public section: {section['id']}")
        if resource["type"] not in component["resource_types"]:
            raise SiteStudioContractError(
                f"component/resource mismatch: {section['id']}"
            )
        errors = list(
            Draft202012Validator(component["data_schema"]).iter_errors(resource["data"])
        )
        if errors:
            raise SiteStudioContractError(
                f"invalid {resource['id']} data: {errors[0].message}"
            )
        anchor = f"routes.{route['id']}.sections.{section['id']}"
        widgets.append(
            {
                "id": section["id"],
                "type": section["component"],
                "area": "site-content",
                "inputs": {
                    "anchorId": section.get("anchor_id", section["id"]),
                    "resourceRef": section["resource_ref"],
                    "sourceAnchor": anchor,
                    "data": deepcopy(resource["data"]),
                },
            }
        )
        entries.append(
            {
                "widget_id": section["id"],
                "component": section["component"],
                "route_anchor": anchor,
                "resource_ref": section["resource_ref"],
                "resource_anchor": f"resources.{section['resource_ref']}",
                "theme_anchor": "theme.tokens",
            }
        )
        public_refs.append(
            {"id": resource["id"], "ref": f"../resources/{resource['id']}.json"}
        )
    page = {
        "id": f"{source['id']}.{route['id']}",
        "title": route["metadata"]["title"],
        "version": "adaos.webui.v1",
        "release_stage": "DEVELOPMENT",
        "surfaceClass": "site",
        "surfaceObjectKind": "public_site_route",
        "layout": {
            "version": 2,
            "pattern": "document",
            "density": "comfortable",
            "contentWidth": "fluid",
            "scroll": "page",
            "regions": [
                {
                    "id": "site-content",
                    "role": "main",
                    "scroll": "page",
                    "presentation": {"wide": "stack", "compact": "stack"},
                }
            ],
        },
        "widgets": widgets,
        "_adaos": {
            "source_digest": source_digest,
            "route_id": route["id"],
            "source_map_ref": f"../source-maps/{route['id']}.source-map.json",
        },
    }
    route_artifact = {
        "schema": "adaos.site_route.v1",
        "site_id": source["id"],
        "route_id": route["id"],
        "path": route["path"],
        "locale": source["locale"],
        "canonical_url": route["canonical_url"],
        "source_digest": source_digest,
        "head": deepcopy(route["metadata"]),
        "json_ld": deepcopy(route["json_ld"]),
        "visible_content": _visible(
            [
                resources[section["resource_ref"]]["data"]
                for section in route["sections"]
            ]
        ),
        "runtime_graph_ref": f"../pages/{route['id']}.webui.json",
        "public_resource_refs": list(
            {entry["id"]: entry for entry in public_refs}.values()
        ),
        "source_map_ref": f"../source-maps/{route['id']}.source-map.json",
    }
    validate_contract("site_route.v1.schema.json", route_artifact)
    return {
        "page": page,
        "route": route_artifact,
        "source_map": {
            "schema": "adaos.site_source_map.v1",
            "site_id": source["id"],
            "route_id": route["id"],
            "source_digest": source_digest,
            "entries": entries,
        },
    }


def _compile_assets(assets, resolver):
    compiled = []
    for asset in assets:
        declared = str(asset.get("digest") or "")
        if resolver is None:
            raise SiteStudioContractError(f"asset resolver is required: {asset['id']}")
        raw = resolver(asset)
        if not isinstance(raw, bytes) or digest(raw) != declared:
            raise SiteStudioContractError(
                f"unresolved or mismatched asset: {asset['id']}"
            )
        compiled.append(
            {
                "id": asset["id"],
                "public_path": asset["public_path"],
                "media_type": asset["media_type"],
                "digest": declared,
                "size_bytes": len(raw),
            }
        )
    return compiled


def _resolve_asset_references(value, asset_paths):
    if isinstance(value, str):
        return asset_paths.get(value, value)
    if isinstance(value, list):
        return [_resolve_asset_references(item, asset_paths) for item in value]
    if isinstance(value, dict):
        return {
            key: _resolve_asset_references(item, asset_paths)
            for key, item in value.items()
        }
    return value


def _compile_theme(tokens):
    unknown = set(tokens) - set(THEME_TOKENS)
    if unknown:
        raise SiteStudioContractError(f"unknown theme token: {sorted(unknown)[0]}")
    variables = {}
    for name, raw in tokens.items():
        value = str(raw).strip()
        if name.startswith("color.") and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise SiteStudioContractError(f"invalid color theme token: {name}")
        if name == "size.maxWidth" and not re.fullmatch(r"[0-9]{3,4}px", value):
            raise SiteStudioContractError("invalid size.maxWidth token")
        variables[THEME_TOKENS[name]] = value
    return {"tokens": deepcopy(tokens), "css_variables": variables}


def _compile_packages(catalog):
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for component in catalog["components"]:
        families[component["family"]].append(component)
    result = []
    catalog_digest = digest(catalog)
    for family in sorted(families):
        components = sorted(families[family], key=lambda item: item["id"])
        body = {
            "schema": "adaos.site_component_package.v1",
            "package_id": f"{family}@{PACKAGE_VERSION}",
            "family": family,
            "version": PACKAGE_VERSION,
            "renderer_abi": {"minimum": RENDERER_ABI, "maximum": RENDERER_ABI},
            "module_ref": f"builtin:{family}",
            "style_refs": ["builtin:adaos.public-site.v1"],
            "asset_refs": [],
            "catalog_digest": catalog_digest,
            "components": [component["id"] for component in components],
            "contracts": [
                {
                    "id": component["id"],
                    "data_schema": deepcopy(component["data_schema"]),
                    "defaults": deepcopy(component["defaults"]),
                    "editor": deepcopy(component["editor"]),
                    "representative_states": list(
                        component["representative_states"]
                    ),
                    "accessibility": component["accessibility"],
                    "seo": component["seo"],
                    "theme_slots": list(component["theme_slots"]),
                    "source_map": component["source_map"],
                    "migration": deepcopy(component["migration"]),
                }
                for component in components
            ],
            "migration_digest": digest(
                [
                    {"id": component["id"], "migration": component["migration"]}
                    for component in components
                ]
            ),
            "created_at": PACKAGE_QUALIFIED_AT,
        }
        artifact = {**body, "digest": digest(body)}
        validate_contract("site_component_package.v1.schema.json", artifact)
        result.append(
            {
                "artifact_ref": f"components/packages/{family}-{PACKAGE_VERSION}.package.json",
                "artifact": artifact,
            }
        )
    return result


def _compile_release_set(catalog, packages, channel):
    entries = [
        {
            "family": package["artifact"]["family"],
            "package_id": package["artifact"]["package_id"],
            "version": package["artifact"]["version"],
            "digest": package["artifact"]["digest"],
            "artifact_ref": package["artifact_ref"],
        }
        for package in packages
    ]
    qualification_digest = digest(
        {
            "catalog_digest": digest(catalog),
            "renderer_abi": RENDERER_ABI,
            "packages": entries,
        }
    )
    body = {
        "schema": "adaos.site_component_release_set.v1",
        "set_id": f"site-set-{channel}-{qualification_digest[7:23]}",
        "channel": channel,
        "renderer_abi": RENDERER_ABI,
        "status": "candidate",
        "packages": entries,
        "qualified_at": PACKAGE_QUALIFIED_AT,
        "qualification_digest": qualification_digest,
    }
    release_set = {**body, "set_digest": digest(body)}
    validate_contract("site_component_release_set.v1.schema.json", release_set)
    return release_set


def _reject_executable(value, path="source"):
    if isinstance(value, str):
        if re.search(
            r"</?(?:script|style|iframe|object|embed)\b", value, re.I
        ) or value.strip().lower().startswith("javascript:"):
            raise SiteStudioContractError(f"executable content at {path}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_executable(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                raise SiteStudioContractError(f"forbidden static field at {path}.{key}")
            _reject_executable(item, f"{path}.{key}")


def _visible(value):
    result = []

    def visit(item):
        if (
            isinstance(item, str)
            and len(item) > 1
            and not item.startswith(("http", "#", "assets/", "data:"))
        ):
            result.append(item)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, dict):
            for key, nested in item.items():
                if key != "publicKey":
                    visit(nested)

    visit(value)
    return result


def _sitemap(routes):
    entries = "\n".join(
        f"  <url><loc>{route['canonical_url']}</loc></url>" for route in routes
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n"
    )


def _robots(origin):
    return f"User-agent: *\nAllow: /\nSitemap: {str(origin).rstrip('/')}/sitemap.xml\n"


def _route_markdown(route):
    head = route.get("head") or {}
    title = str(head.get("title") or route.get("route_id") or "Route").strip()
    description = str(head.get("description") or "").strip()
    lines = [f"# {title}", "", f"Canonical: {route['canonical_url']}"]
    if description:
        lines.extend(["", description])
    visible = [str(item).strip() for item in route.get("visible_content") or []]
    visible = [item for item in visible if item]
    if visible:
        lines.extend(["", "## Content", ""])
        lines.extend(f"- {item}" for item in visible)
    resources = route.get("public_resource_refs") or []
    if resources:
        lines.extend(["", "## Structured data", ""])
        lines.extend(
            f"- [{item['id']}]({item['ref']})" for item in resources
        )
    return "\n".join(lines) + "\n"


def _llms_txt(discovery, artifacts):
    lines = [f"# {discovery['name']}", "", f"Canonical: {discovery['canonical_origin']}"]
    routes = discovery.get("routes") or []
    if routes:
        lines.extend(["", "## Routes", ""])
        for item in routes:
            route_ref = str(item.get("artifact_ref") or "").removeprefix("../")
            route = artifacts.get(route_ref) or {}
            head = route.get("head") or {}
            title = str(head.get("title") or item["id"])
            description = str(head.get("description") or "").strip()
            suffix = f": {description}" if description else ""
            lines.append(f"- [{title}]({item['markdown_ref']}){suffix}")
    resources = discovery.get("resources") or []
    if resources:
        lines.extend(["", "## Structured data", ""])
        lines.extend(
            f"- [{item['id']}]({item['ref']}) ({item['type']})"
            for item in resources
        )
    lines.extend(
        [
            "",
            "## Discovery",
            "",
            "- [Site discovery](.well-known/adaos-site.json)",
            "- [Sitemap](sitemap.xml)",
        ]
    )
    return "\n".join(lines) + "\n"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
