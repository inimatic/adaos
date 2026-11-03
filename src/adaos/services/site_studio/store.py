from __future__ import annotations

from copy import deepcopy
import json
import sqlite3
from typing import Any, Mapping

from .compiler import compile_site_revision
from .contracts import canonical_json, digest, validate_contract


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
            "INSERT INTO site_studio_change_sets VALUES(?,?,?)",
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
    if (
        preview.get("deployment", {}).get("deployment_digest")
        != project["preview_deployment_digest"]
        or preview.get("source", {}).get("revision", {}).get("id")
        != project["active_revision_id"]
        or digest(preview.get("source")) != project["active_source_digest"]
    ):
        raise ValueError("active preview pointer failed integrity verification")
    return preview


def _project(db: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT body FROM site_studio_projects WHERE project_id=?", (project_id,)
    ).fetchone()
    return json.loads(row[0]) if row else None
