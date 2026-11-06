from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Any, Mapping, Sequence

from .compiler import default_component_catalog
from .contracts import canonical_json, digest, validate_contract
from .store import (
    change_set_history,
    current_authoring_context,
    current_preview,
    ensure_store,
    material_inventory,
    project_state,
    propose_change_set,
)


MATERIAL_MAX_BYTES = 20 * 1024 * 1024
_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$")
_SOURCE_ANCHOR = re.compile(
    r"^(?:identity\.[A-Za-z][A-Za-z0-9_.]*|theme\.tokens\.[A-Za-z][A-Za-z0-9.]*|"
    r"routes\.[a-z][a-z0-9-]{1,63}(?:\.(?:metadata|sections)(?:\.[A-Za-z0-9_.-]+)*)?|"
    r"resources\.[a-z][a-z0-9-]{1,63}(?:\.data(?:\.[A-Za-z0-9_.-]+)*)?|"
    r"assets\.[a-z][a-z0-9-]{1,63})$"
)
_ORIGINS = {"chat", "visual", "dev_ticket", "form", "llm", "system"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _identifier(value: str, label: str) -> str:
    token = str(value or "").strip()
    if not _ID.fullmatch(token):
        raise ValueError(f"{label} is invalid")
    return token


def _object_ref(content_digest: str) -> str:
    return "material-objects/sha256/" + content_digest.removeprefix("sha256:")


def intake_material(
    db: sqlite3.Connection,
    *,
    project_id: str,
    material_id: str,
    role: str,
    media_type: str,
    content: bytes,
    provenance: str,
    rights: str,
    public_eligible: bool,
    usage_refs: Sequence[str] = (),
    preview: bytes | None = None,
    preview_media_type: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Admit one immutable material without trusting instructions embedded in it."""

    ensure_store(db)
    project_id = _identifier(project_id, "project_id")
    material_id = _identifier(material_id, "material_id")
    role = str(role or "").strip()
    if role not in {"authoritative", "orientation"}:
        raise ValueError("material role must be authoritative or orientation")
    media_type = str(media_type or "").strip().lower()
    if not _MEDIA_TYPE.fullmatch(media_type):
        raise ValueError("material media_type is invalid")
    if not isinstance(content, bytes) or not content or len(content) > MATERIAL_MAX_BYTES:
        raise ValueError("material content must contain at most 20 MiB")
    provenance = str(provenance or "").strip()
    rights = str(rights or "").strip()
    if not provenance or len(provenance) > 8000:
        raise ValueError("bounded material provenance is required")
    if not rights or len(rights) > 8000:
        raise ValueError("bounded material rights notes are required")
    refs = sorted({str(item or "").strip() for item in usage_refs if str(item or "").strip()})
    if len(refs) > 500 or any(not _SOURCE_ANCHOR.fullmatch(item) for item in refs):
        raise ValueError("material usage refs must be bounded source anchors")

    content_digest = digest(content)
    record: dict[str, Any] = {
        "schema": "adaos.site_studio.material.v1",
        "material_id": material_id,
        "project_id": project_id,
        "role": role,
        "media_type": media_type,
        "content_digest": content_digest,
        "size_bytes": len(content),
        "object_ref": _object_ref(content_digest),
        "provenance": provenance,
        "rights": rights,
        "public_eligible": bool(public_eligible),
        "instruction_trust": "untrusted",
        "model_authority": "reference_only",
        "cache_scope": "public-eligible" if public_eligible else "private",
        "usage_refs": refs,
        "created_at": str(created_at or _now()),
    }
    preview_bytes = preview
    if preview_bytes is None and media_type.startswith(("image/", "text/")):
        preview_bytes = content if len(content) <= 1024 * 1024 else content[: 1024 * 1024]
        preview_media_type = media_type
    if preview_bytes is not None:
        if not isinstance(preview_bytes, bytes) or not preview_bytes or len(preview_bytes) > MATERIAL_MAX_BYTES:
            raise ValueError("material preview must contain at most 20 MiB")
        preview_type = str(preview_media_type or media_type).strip().lower()
        if not _MEDIA_TYPE.fullmatch(preview_type):
            raise ValueError("material preview media_type is invalid")
        preview_digest = digest(preview_bytes)
        record["preview"] = {
            "media_type": preview_type,
            "digest": preview_digest,
            "size_bytes": len(preview_bytes),
            "object_ref": _object_ref(preview_digest),
        }
    validate_contract("site_studio_material.v1.schema.json", record)

    current = db.execute(
        "SELECT body FROM site_studio_project_materials WHERE project_id=? AND material_id=?",
        (project_id, material_id),
    ).fetchone()
    if current and json.loads(current[0]) != record:
        raise ValueError("material identity already refers to different content")
    db.execute("SAVEPOINT site_studio_material_intake")
    try:
        _retain_object(db, content_digest, media_type, content)
        if preview_bytes is not None:
            _retain_object(
                db,
                record["preview"]["digest"],
                record["preview"]["media_type"],
                preview_bytes,
            )
        db.execute(
            "INSERT INTO site_studio_project_materials(project_id,material_id,digest,body) "
            "VALUES(?,?,?,?) ON CONFLICT(project_id,material_id) DO NOTHING",
            (project_id, material_id, content_digest, canonical_json(record)),
        )
        db.execute(
            "INSERT INTO site_studio_materials(digest,role,body) VALUES(?,?,?) "
            "ON CONFLICT(digest) DO NOTHING",
            (content_digest, role, canonical_json(record)),
        )
        db.execute("RELEASE SAVEPOINT site_studio_material_intake")
    except BaseException:
        db.execute("ROLLBACK TO SAVEPOINT site_studio_material_intake")
        db.execute("RELEASE SAVEPOINT site_studio_material_intake")
        raise
    return record


def _retain_object(
    db: sqlite3.Connection, content_digest: str, media_type: str, content: bytes
) -> None:
    row = db.execute(
        "SELECT media_type,size_bytes,body FROM site_studio_material_objects WHERE digest=?",
        (content_digest,),
    ).fetchone()
    if row and (row[0] != media_type or int(row[1]) != len(content) or bytes(row[2]) != content):
        raise ValueError("content-addressed material object collision")
    db.execute(
        "INSERT INTO site_studio_material_objects(digest,media_type,size_bytes,body) "
        "VALUES(?,?,?,?) ON CONFLICT(digest) DO NOTHING",
        (content_digest, media_type, len(content), content),
    )


def read_material_object(db: sqlite3.Connection, object_ref: str) -> dict[str, Any]:
    ensure_store(db)
    match = re.fullmatch(r"material-objects/sha256/([a-f0-9]{64})", str(object_ref or ""))
    if not match:
        raise ValueError("material object ref is not content-addressed")
    content_digest = "sha256:" + match.group(1)
    row = db.execute(
        "SELECT media_type,size_bytes,body FROM site_studio_material_objects WHERE digest=?",
        (content_digest,),
    ).fetchone()
    if not row or digest(bytes(row[2])) != content_digest or int(row[1]) != len(row[2]):
        raise ValueError("material object is unavailable or failed integrity verification")
    return {
        "digest": content_digest,
        "media_type": row[0],
        "size_bytes": int(row[1]),
        "content": bytes(row[2]),
    }


def update_material_usage(
    db: sqlite3.Connection,
    *,
    project_id: str,
    material_id: str,
    usage_refs: Sequence[str],
) -> dict[str, Any]:
    ensure_store(db)
    refs = sorted({str(item or "").strip() for item in usage_refs if str(item or "").strip()})
    if len(refs) > 500 or any(not _SOURCE_ANCHOR.fullmatch(item) for item in refs):
        raise ValueError("material usage refs must be bounded source anchors")
    row = db.execute(
        "SELECT body FROM site_studio_project_materials WHERE project_id=? AND material_id=?",
        (project_id, material_id),
    ).fetchone()
    if not row:
        raise ValueError("material is unavailable")
    record = json.loads(row[0])
    record["usage_refs"] = refs
    validate_contract("site_studio_material.v1.schema.json", record)
    db.execute(
        "UPDATE site_studio_project_materials SET body=? WHERE project_id=? AND material_id=?",
        (canonical_json(record), project_id, material_id),
    )
    return record


def authoring_projection(
    db: sqlite3.Connection,
    project_id: str,
    *,
    source_anchor: str | None = None,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the retained source into one route/resource/asset authoring model."""

    ensure_store(db)
    project = project_state(db, project_id)
    if not project:
        raise ValueError(f"site project not found: {project_id}")
    preview = current_preview(db, project_id)
    source = deepcopy(preview["source"])
    component_catalog = deepcopy(dict(catalog or default_component_catalog()))
    components = {item["id"]: item for item in component_catalog["components"]}
    resources = {item["id"]: item for item in source["resources"]}
    routes = []
    for route in source["routes"]:
        sections = []
        for section in route["sections"]:
            resource = resources[section["resource_ref"]]
            anchor = f"routes.{route['id']}.sections.{section['id']}"
            sections.append(
                {
                    "id": section["id"],
                    "kind": "section",
                    "label": _resource_label(resource),
                    "component": section["component"],
                    "resource_id": resource["id"],
                    "source_anchor": anchor,
                    "rendered_widget_id": section["id"],
                }
            )
        routes.append(
            {
                "id": route["id"],
                "kind": "route",
                "label": route["metadata"]["title"],
                "path": route["path"],
                "source_anchor": f"routes.{route['id']}",
                "sections": sections,
            }
        )
    selected = source_anchor or f"routes.{source['routes'][0]['id']}"
    return {
        "schema": "adaos.site_studio.authoring_projection.v1",
        "project": project,
        "development_path": f"/site-development/{source['id']}/",
        "source_digest": project["active_source_digest"],
        "projection_digest": preview["projection"]["projection_digest"],
        "component_set": {
            "id": preview["component_release_set"]["set_id"],
            "digest": preview["component_release_set"]["set_digest"],
            "renderer_abi": preview["deployment"]["renderer_abi"],
        },
        "tree": {
            "routes": routes,
            "resources": [
                {
                    "id": item["id"],
                    "kind": "resource",
                    "label": _resource_label(item),
                    "resource_type": item["type"],
                    "source_anchor": f"resources.{item['id']}",
                }
                for item in source["resources"]
            ],
            "assets": [
                {
                    "id": item["id"],
                    "kind": "asset",
                    "label": item.get("alt") or item["id"],
                    "media_type": item["media_type"],
                    "digest": item.get("digest"),
                    "source_anchor": f"assets.{item['id']}",
                }
                for item in source["assets"]
            ],
            "materials": material_inventory(db, project_id),
        },
        "selection": inspect_source_anchor(
            source, selected, catalog=component_catalog, components=components
        ),
        "diagnostics": authoring_diagnostics(
            db, project_id, preview=preview, catalog=component_catalog
        ),
    }


def inspect_source_anchor(
    source: Mapping[str, Any],
    source_anchor: str,
    *,
    catalog: Mapping[str, Any] | None = None,
    components: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    catalog = deepcopy(dict(catalog or default_component_catalog()))
    component_map = dict(components or {item["id"]: item for item in catalog["components"]})
    parts = str(source_anchor or "").split(".")
    if len(parts) == 4 and parts[0] == "routes" and parts[2] == "sections":
        route = _by_id(source["routes"], parts[1], "route")
        section = _by_id(route["sections"], parts[3], "section")
        resource = _by_id(source["resources"], section["resource_ref"], "resource")
        contract = component_map.get(section["component"])
        if not contract:
            raise ValueError("selected component contract is unavailable")
        return {
            "kind": "section",
            "source_anchor": source_anchor,
            "value": deepcopy(section),
            "resource": deepcopy(resource),
            "schema": deepcopy(contract["data_schema"]),
            "editor": deepcopy(contract["editor"]),
            "allowed_operations": [
                "set_resource_field",
                "assign_asset",
                "move_section",
                "remove_section",
            ],
        }
    if len(parts) == 2 and parts[0] == "routes":
        route = _by_id(source["routes"], parts[1], "route")
        return {
            "kind": "route",
            "source_anchor": source_anchor,
            "value": deepcopy(route),
            "schema": {"type": "object", "properties": {"path": {"type": "string"}, "metadata": {"type": "object"}, "json_ld": {"type": "array"}}},
            "editor": {"fields": ["path", "metadata", "json_ld"]},
            "allowed_operations": ["set_route_metadata", "add_section", "remove_route"],
        }
    if len(parts) == 2 and parts[0] == "resources":
        resource = _by_id(source["resources"], parts[1], "resource")
        contracts = [item for item in catalog["components"] if resource["type"] in item["resource_types"]]
        return {
            "kind": "resource",
            "source_anchor": source_anchor,
            "value": deepcopy(resource),
            "schema": deepcopy(contracts[0]["data_schema"] if contracts else {"type": "object"}),
            "editor": deepcopy(contracts[0]["editor"] if contracts else {"fields": []}),
            "allowed_operations": ["set_resource_field", "remove_resource"],
        }
    if len(parts) == 2 and parts[0] == "assets":
        asset = _by_id(source["assets"], parts[1], "asset")
        return {
            "kind": "asset",
            "source_anchor": source_anchor,
            "value": deepcopy(asset),
            "schema": {"type": "object", "required": ["id", "media_type", "digest"]},
            "editor": {"fields": ["alt", "provenance", "rights"]},
            "allowed_operations": ["replace_asset", "remove_asset", "assign_asset"],
        }
    if source_anchor == "theme.tokens":
        return {
            "kind": "theme",
            "source_anchor": source_anchor,
            "value": deepcopy(source["theme"]["tokens"]),
            "schema": {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean"]}},
            "editor": {"control": "theme_tokens"},
            "allowed_operations": ["set_theme_token"],
        }
    raise ValueError("source anchor is not inspectable")


def authoring_diagnostics(
    db: sqlite3.Connection,
    project_id: str,
    *,
    preview: Mapping[str, Any] | None = None,
    catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_store(db)
    current = deepcopy(dict(preview or current_preview(db, project_id)))
    context = current_authoring_context(db, project_id) or {}
    source = current["source"]
    catalog = deepcopy(dict(catalog or default_component_catalog()))
    history = change_set_history(db, project_id)
    latest = history[0] if history else {}
    artifact_row = db.execute(
        "SELECT body FROM site_studio_artifacts WHERE digest=(SELECT artifact_digest "
        "FROM site_studio_revisions WHERE revision_id=?) AND kind='compiled_site_revision'",
        (source["revision"]["id"],),
    ).fetchone()
    artifacts = json.loads(artifact_row[0]) if artifact_row else {}
    source_maps = [
        value
        for key, value in sorted(artifacts.items())
        if key.startswith("source-maps/") and isinstance(value, dict)
    ]
    outcome = dict(context.get("outcome") or {})
    material_by_id = {item["material_id"]: item for item in material_inventory(db, project_id)}
    used_materials = [
        item
        for item in material_by_id.values()
        if item.get("usage_refs")
    ]
    route_findings = []
    for route in source["routes"]:
        anchors = [section.get("anchor_id") or section["id"] for section in route["sections"]]
        if len(anchors) != len(set(anchors)):
            route_findings.append({"severity": "error", "code": "duplicate_anchor", "route_id": route["id"]})
    return {
        "schema": "adaos.site_studio.diagnostics.v1",
        "route_map": [
            {"id": route["id"], "path": route["path"], "canonical_url": route["canonical_url"]}
            for route in source["routes"]
        ],
        "source_materials_used": [
            {
                "material_id": item["material_id"],
                "digest": item["content_digest"],
                "role": item["role"],
                "usage_refs": item["usage_refs"],
            }
            for item in used_materials
        ],
        "assumptions": _lines(outcome.get("constraints")),
        "questions": _lines(outcome.get("open_questions")),
        "source_maps": source_maps,
        "changed_resources": sorted(
            {
                target.split(".")[1]
                for target in latest.get("source_map_targets", [])
                if target.startswith("resources.") and len(target.split(".")) > 1
            }
        ),
        "digests": {
            "source": current["projection"]["source_digest"],
            "projection": current["projection"]["projection_digest"],
            "deployment": current["deployment"]["deployment_digest"],
            "component_set": current["component_release_set"]["set_digest"],
        },
        "route_metadata": [
            {"route_id": route["id"], "metadata": deepcopy(route["metadata"]), "json_ld": deepcopy(route["json_ld"])}
            for route in source["routes"]
        ],
        "discovery": deepcopy(artifacts.get(".well-known/adaos-site.json")),
        "theme_tokens": deepcopy(source["theme"]["tokens"]),
        "unsupported_capabilities": list(current["projection"]["policy"]["denied_data_sources"]),
        "responsive": {
            "states": sorted(
                {state for component in catalog["components"] for state in component["representative_states"]}
            ),
            "findings": route_findings,
        },
        "accessibility": {
            "contracts": [
                {"component": component["id"], "behavior": component["accessibility"]}
                for component in catalog["components"]
            ],
            "findings": route_findings,
        },
    }


def propose_semantic_changes(
    db: sqlite3.Connection,
    *,
    project_id: str,
    change_set_id: str,
    actor_id: str,
    actor_kind: str,
    origin: str,
    operations: Sequence[Mapping[str, Any]],
    rationale: str,
    source_map_targets: Sequence[str] | None = None,
    request_ref: str | None = None,
    created_at: str | None = None,
    catalog: Mapping[str, Any] | None = None,
    asset_resolver=None,
) -> dict[str, Any]:
    """Converge chat, visual, ticket, form, and LLM edits on one proposal path."""

    project = project_state(db, project_id)
    if not project:
        raise ValueError(f"site project not found: {project_id}")
    origin = str(origin or "").strip()
    if origin not in _ORIGINS:
        raise ValueError("semantic change origin is unsupported")
    operation_list = [deepcopy(dict(item)) for item in operations]
    targets = sorted(
        set(
            source_map_targets
            or [str(item.get("target") or "") for item in operation_list]
        )
    )
    change_set = {
        "schema": "adaos.site_change_set.v1",
        "change_set_id": _identifier(change_set_id, "change_set_id"),
        "project_id": _identifier(project_id, "project_id"),
        "base_revision_id": project["active_revision_id"],
        "base_source_digest": project["active_source_digest"],
        "actor": {"id": str(actor_id or "").strip(), "kind": actor_kind},
        "origin": {
            "channel": origin,
            **({"request_ref": str(request_ref)[:512]} if request_ref else {}),
        },
        "source_map_targets": targets,
        "operations": operation_list,
        "rationale": str(rationale or "").strip(),
        "validation": {"status": "pending", "findings": []},
        "status": "proposed",
        "created_at": str(created_at or _now()),
    }
    validate_contract("site_change_set.v1.schema.json", change_set)
    return propose_change_set(
        db,
        project_id=project_id,
        change_set=change_set,
        catalog=catalog,
        asset_resolver=asset_resolver,
    )


def _resource_label(resource: Mapping[str, Any]) -> str:
    data = resource.get("data")
    if isinstance(data, Mapping):
        for key in ("title", "name", "statement", "brand"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:160]
    return str(resource["id"])


def _by_id(values: Sequence[Mapping[str, Any]], identity: str, label: str) -> dict[str, Any]:
    value = next((item for item in values if item.get("id") == identity), None)
    if value is None:
        raise ValueError(f"{label} not found: {identity}")
    return deepcopy(dict(value))


def _lines(value: Any) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()][:100]
