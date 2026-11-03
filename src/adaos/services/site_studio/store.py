from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
from typing import Any, Mapping

from .compiler import compile_site_revision
from .contracts import apply_change_set, canonical_json, digest, validate_contract


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS site_studio_projects (
      project_id TEXT PRIMARY KEY,
      body TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS site_studio_revisions (
      revision_id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL,
      source_digest TEXT NOT NULL UNIQUE,
      source_body TEXT NOT NULL,
      artifact_digest TEXT NOT NULL,
      body TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS site_studio_change_sets (
      change_set_id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL,
      body TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS site_studio_artifacts (
      digest TEXT PRIMARY KEY,
      kind TEXT NOT NULL,
      body TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS site_studio_contexts (
      digest TEXT PRIMARY KEY,
      body TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS site_studio_materials (
      digest TEXT PRIMARY KEY,
      role TEXT NOT NULL,
      body TEXT NOT NULL
    )
    """,
)


def ensure_store(db: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        db.execute(statement)


def commit_revision(
    db: sqlite3.Connection,
    *,
    project_id: str,
    title: str,
    source: Mapping[str, Any],
    authoring_context: Mapping[str, Any],
    change_set: Mapping[str, Any],
    catalog: Mapping[str, Any] | None = None,
    asset_resolver=None,
) -> dict[str, Any]:
    """Compile first, then atomically retain immutable state and move preview."""

    ensure_store(db)
    validate_contract("site_studio_authoring_context.v1.schema.json", authoring_context)
    validate_contract("site_change_set.v1.schema.json", change_set)
    if change_set["project_id"] != project_id:
        raise ValueError("change set project mismatch")
    parent = _project(db, project_id)
    expected_base = parent["active_source_digest"] if parent else digest(source)
    if change_set["base_source_digest"] != expected_base:
        raise ValueError("change set does not target the active immutable revision")
    if parent:
        row = db.execute(
            "SELECT source_body FROM site_studio_revisions WHERE revision_id=?",
            (parent["active_revision_id"],),
        ).fetchone()
        if not row:
            raise ValueError("active source revision is unavailable")
        replayed = apply_change_set(json.loads(row[0]), change_set)
        replayed["revision"] = deepcopy(dict(source))["revision"]
        if digest(replayed) != digest(source):
            raise ValueError(
                "committed source does not match semantic change set replay"
            )
    compiled = compile_site_revision(
        source, catalog=catalog, asset_resolver=asset_resolver
    )
    source_digest = compiled["source_digest"]
    revision_id = compiled["source"]["revision"]["id"]
    created_at = compiled["source"]["revision"]["created_at"]
    context_digest = digest(authoring_context)
    artifact_digest = compiled["artifact_digest"]
    preview = compiled["preview"]
    committed_change_set = deepcopy(dict(change_set))
    committed_change_set.update(
        {
            "validation": {
                "status": "passed",
                "findings": [],
                "report_digest": digest(
                    {"checks": "canonical-static-site", "status": "passed"}
                ),
            },
            "status": "committed",
            "preview": {
                "source_digest": source_digest,
                "projection_digest": compiled["projection_digest"],
                "deployment_digest": compiled["deployment_digest"],
                "component_set_digest": compiled["component_set_digest"],
                "renderer_abi": compiled["deployment"]["renderer_abi"],
            },
            "commit_receipt": {
                "revision_id": revision_id,
                "source_digest": source_digest,
                "artifact_digest": artifact_digest,
                "committed_at": created_at,
            },
        }
    )
    validate_contract("site_change_set.v1.schema.json", committed_change_set)
    revision = {
        "schema": "adaos.site_studio.revision.v1",
        "revision_id": revision_id,
        "project_id": project_id,
        **(
            {"parent_revision_id": parent["active_revision_id"]}
            if parent and parent.get("active_revision_id")
            else {}
        ),
        "source_digest": source_digest,
        "source_ref": f"revisions/{revision_id}/site.source.json",
        "artifact_digest": artifact_digest,
        "artifact_ref": f"revisions/{revision_id}/artifacts.json",
        "component_set_digest": compiled["component_set_digest"],
        "renderer_abi": compiled["deployment"]["renderer_abi"],
        "change_set_id": committed_change_set["change_set_id"],
        "authoring_context_digest": context_digest,
        "created_at": created_at,
    }
    validate_contract("site_studio_revision.v1.schema.json", revision)
    project = {
        "schema": "adaos.site_studio.project.v1",
        "project_id": project_id,
        "site_id": compiled["source"]["id"],
        "title": title,
        "active_revision_id": revision_id,
        "active_source_digest": source_digest,
        "preview_deployment_digest": compiled["deployment_digest"],
        "created_at": parent["created_at"] if parent else created_at,
        "updated_at": created_at,
    }
    validate_contract("site_studio_project.v1.schema.json", project)

    db.execute("SAVEPOINT site_studio_commit")
    try:
        db.execute(
            "INSERT INTO site_studio_contexts(digest,body) VALUES(?,?) ON CONFLICT(digest) DO NOTHING",
            (context_digest, canonical_json(authoring_context)),
        )
        for material in authoring_context["materials"]:
            db.execute(
                "INSERT INTO site_studio_materials(digest,role,body) VALUES(?,?,?) "
                "ON CONFLICT(digest) DO NOTHING",
                (material["digest"], material["role"], canonical_json(material)),
            )
        db.execute(
            "INSERT INTO site_studio_artifacts(digest,kind,body) VALUES(?,?,?) ON CONFLICT(digest) DO NOTHING",
            (
                artifact_digest,
                "compiled_site_revision",
                canonical_json(compiled["artifacts"]),
            ),
        )
        db.execute(
            "INSERT INTO site_studio_artifacts(digest,kind,body) VALUES(?,?,?) ON CONFLICT(digest) DO NOTHING",
            (compiled["deployment_digest"], "site_preview", canonical_json(preview)),
        )
        db.execute(
            "INSERT INTO site_studio_revisions VALUES(?,?,?,?,?,?)",
            (
                revision_id,
                project_id,
                source_digest,
                canonical_json(compiled["source"]),
                artifact_digest,
                canonical_json(revision),
            ),
        )
        db.execute(
            "INSERT INTO site_studio_change_sets VALUES(?,?,?) "
            "ON CONFLICT(change_set_id) DO UPDATE SET body=excluded.body",
            (
                committed_change_set["change_set_id"],
                project_id,
                canonical_json(committed_change_set),
            ),
        )
        db.execute(
            "INSERT INTO site_studio_projects VALUES(?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET body=excluded.body",
            (project_id, canonical_json(project)),
        )
        db.execute("RELEASE SAVEPOINT site_studio_commit")
    except BaseException:
        db.execute("ROLLBACK TO SAVEPOINT site_studio_commit")
        db.execute("RELEASE SAVEPOINT site_studio_commit")
        raise
    return {
        "project": project,
        "revision": revision,
        "change_set": committed_change_set,
        "preview": preview,
    }


def current_preview(db: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    ensure_store(db)
    project = _project(db, project_id)
    if not project:
        raise ValueError(f"site project not found: {project_id}")
    row = db.execute(
        "SELECT body FROM site_studio_artifacts WHERE digest=? AND kind='site_preview'",
        (project["preview_deployment_digest"],),
    ).fetchone()
    if not row:
        raise ValueError("active preview artifact is unavailable")
    preview = json.loads(row[0])
    if not _preview_integrity_matches(preview, project):
        raise ValueError("active preview pointer failed integrity verification")
    return preview


def project_state(db: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    ensure_store(db)
    return _project(db, project_id)


def revision_history(db: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    ensure_store(db)
    return [
        json.loads(row[0])
        for row in db.execute(
            "SELECT body FROM site_studio_revisions WHERE project_id=? ORDER BY rowid DESC",
            (project_id,),
        )
    ]


def change_set_history(db: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    ensure_store(db)
    return [
        json.loads(row[0])
        for row in db.execute(
            "SELECT body FROM site_studio_change_sets WHERE project_id=? ORDER BY rowid DESC",
            (project_id,),
        )
    ]


def material_inventory(db: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_store(db)
    return [
        json.loads(row[0])
        for row in db.execute(
            "SELECT body FROM site_studio_materials ORDER BY rowid DESC"
        )
    ]


def current_authoring_context(
    db: sqlite3.Connection, project_id: str
) -> dict[str, Any] | None:
    ensure_store(db)
    project = _project(db, project_id)
    if not project:
        return None
    row = db.execute(
        "SELECT c.body FROM site_studio_revisions r "
        "JOIN site_studio_contexts c "
        "ON c.digest=json_extract(r.body,'$.authoring_context_digest') "
        "WHERE r.revision_id=?",
        (project["active_revision_id"],),
    ).fetchone()
    return json.loads(row[0]) if row else None


def propose_change_set(
    db: sqlite3.Connection,
    *,
    project_id: str,
    change_set: Mapping[str, Any],
    catalog: Mapping[str, Any] | None = None,
    asset_resolver=None,
) -> dict[str, Any]:
    """Retain a proposal and preview it without moving the active pointer."""

    ensure_store(db)
    validate_contract("site_change_set.v1.schema.json", change_set)
    if change_set["project_id"] != project_id or change_set["status"] != "proposed":
        raise ValueError("invalid proposed change set state")
    project = _project(db, project_id)
    if (
        not project
        or change_set["base_source_digest"] != project["active_source_digest"]
    ):
        raise ValueError("change set does not target the active immutable revision")
    row = db.execute(
        "SELECT source_body FROM site_studio_revisions WHERE revision_id=?",
        (project["active_revision_id"],),
    ).fetchone()
    if not row:
        raise ValueError("active source revision is unavailable")
    candidate = apply_change_set(json.loads(row[0]), change_set)
    candidate["revision"] = {
        "id": f"proposal-{digest(change_set)[7:23]}",
        "created_at": change_set["created_at"],
        "prompt_packet_ref": f"change-sets/{change_set['change_set_id']}.json",
        "base_digest": project["active_source_digest"],
    }
    compiled = compile_site_revision(
        candidate, catalog=catalog, asset_resolver=asset_resolver
    )
    db.execute(
        "INSERT INTO site_studio_change_sets VALUES(?,?,?) "
        "ON CONFLICT(change_set_id) DO UPDATE SET body=excluded.body",
        (change_set["change_set_id"], project_id, canonical_json(change_set)),
    )
    return {
        "change_set": deepcopy(dict(change_set)),
        "preview": compiled["preview"],
        "diagnostics": compiled["preview"]["diagnostics"],
    }


def commit_proposed_change_set(
    db: sqlite3.Connection,
    *,
    project_id: str,
    change_set_id: str,
    revision_id: str,
    created_at: str,
    authoring_context: Mapping[str, Any],
    title: str,
    catalog: Mapping[str, Any] | None = None,
    asset_resolver=None,
) -> dict[str, Any]:
    """Apply one retained proposal against its immutable base and advance atomically."""

    ensure_store(db)
    row = db.execute(
        "SELECT body FROM site_studio_change_sets WHERE change_set_id=? AND project_id=?",
        (change_set_id, project_id),
    ).fetchone()
    project = _project(db, project_id)
    if not row or not project:
        raise ValueError("proposed change set is unavailable")
    change_set = json.loads(row[0])
    source_row = db.execute(
        "SELECT source_body FROM site_studio_revisions WHERE revision_id=?",
        (project["active_revision_id"],),
    ).fetchone()
    if not source_row:
        raise ValueError("active source revision is unavailable")
    source = apply_change_set(json.loads(source_row[0]), change_set)
    source["revision"] = {
        "id": revision_id,
        "created_at": created_at,
        "prompt_packet_ref": f"change-sets/{change_set_id}.json",
        "base_digest": project["active_source_digest"],
    }
    context = deepcopy(dict(authoring_context))
    context["source_digest"] = digest(source)
    context["created_at"] = created_at
    return commit_revision(
        db,
        project_id=project_id,
        title=title,
        source=source,
        authoring_context=context,
        change_set=change_set,
        catalog=catalog,
        asset_resolver=asset_resolver,
    )


def _preview_integrity_matches(
    preview: Mapping[str, Any], project: Mapping[str, Any]
) -> bool:
    try:
        source = preview["source"]
        projection = preview["projection"]
        deployment = preview["deployment"]
        release_set = preview["component_release_set"]
        packages = preview["component_packages"]
        route = preview["route"]
        page = preview["page"]
        source_map = preview["source_map"]
        projection_body = {
            key: value
            for key, value in projection.items()
            if key != "projection_digest"
        }
        deployment_body = {
            key: value
            for key, value in deployment.items()
            if key != "deployment_digest"
        }
        release_set_body = {
            key: value for key, value in release_set.items() if key != "set_digest"
        }
        route_ref = next(
            item
            for item in projection["routes"]
            if item["id"] == route["route_id"] and item["path"] == route["path"]
        )
        package_entries = {item["family"]: item for item in release_set["packages"]}
        required = {
            item["family"]: item["digest"] for item in deployment["required_packages"]
        }
        admitted = {item["family"]: item["digest"] for item in packages}
        package_integrity = all(
            digest({key: value for key, value in package.items() if key != "digest"})
            == package["digest"]
            and package_entries[package["family"]]["digest"] == package["digest"]
            and package["renderer_abi"]["minimum"] == deployment["renderer_abi"]
            and package["renderer_abi"]["maximum"] == deployment["renderer_abi"]
            for package in packages
        )
        return bool(
            preview.get("schema") == "adaos.site_preview.v1"
            and deployment["deployment_digest"] == project["preview_deployment_digest"]
            and source["revision"]["id"] == project["active_revision_id"]
            and digest(source)
            == project["active_source_digest"]
            == projection["source_digest"]
            and digest(projection_body) == projection["projection_digest"]
            and digest(deployment_body) == deployment["deployment_digest"]
            and deployment["projection_digest"] == projection["projection_digest"]
            and digest(release_set_body) == release_set["set_digest"]
            and deployment["component_release_set"]
            == {"id": release_set["set_id"], "digest": release_set["set_digest"]}
            and required == admitted
            and package_integrity
            and digest(route) == route_ref["artifact_digest"]
            and digest(page) == route_ref["page_graph_digest"]
            and digest(source_map) == route_ref["source_map_digest"]
            and digest(projection["theme"]) == deployment["theme_digest"]
            and sorted(asset["digest"] for asset in projection["assets"])
            == sorted(deployment["asset_digests"])
        )
    except (KeyError, StopIteration, TypeError, ValueError):
        return False


def _project(db: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT body FROM site_studio_projects WHERE project_id=?", (project_id,)
    ).fetchone()
    return json.loads(row[0]) if row else None
