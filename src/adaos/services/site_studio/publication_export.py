from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from adaos.services.public_site_delivery import build_delivery_bundle


_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


def export_publication(
    database_path: Path, files_root: Path, output: Path
) -> dict[str, Any]:
    """Export one exact active Site Studio revision for public delivery."""

    database_path = database_path.resolve(strict=True)
    files_root = files_root.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ValueError("publication output must not already exist")
    if not files_root.is_dir():
        raise ValueError("Site Studio files root must be a directory")
    database = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        project_rows = database.execute(
            "SELECT project_id,body FROM site_studio_projects ORDER BY project_id"
        ).fetchall()
        if len(project_rows) != 1:
            raise ValueError("publication requires one retained Site Studio project")
        project_id, raw_project = project_rows[0]
        project = json.loads(raw_project)
        active_revision = str(project.get("active_revision_id") or "")
        revision_row = database.execute(
            "SELECT source_digest,artifact_digest,source_body FROM site_studio_revisions "
            "WHERE project_id=? AND revision_id=?",
            (project_id, active_revision),
        ).fetchone()
        if not revision_row:
            raise ValueError("active Site Studio revision is unavailable")
        source_digest, artifact_digest, raw_source = revision_row
        source = json.loads(raw_source)
        if semantic_digest(source) != source_digest:
            raise ValueError("active Site Studio source digest mismatch")
        artifact_row = database.execute(
            "SELECT body FROM site_studio_artifacts "
            "WHERE digest=? AND kind='compiled_site_revision'",
            (artifact_digest,),
        ).fetchone()
        if not artifact_row:
            raise ValueError("active compiled Site Studio artifact is unavailable")
        artifacts = json.loads(artifact_row[0])
    finally:
        database.close()
    if semantic_digest(artifacts) != artifact_digest:
        raise ValueError("compiled Site Studio artifact digest mismatch")
    output.mkdir(parents=True)
    for relative, value in sorted(artifacts.items()):
        target = admitted_target(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            target.write_text(value, encoding="utf-8", newline="\n")
        else:
            target.write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    projection = artifacts.get("site.projection.json")
    if not isinstance(projection, dict):
        raise ValueError("compiled Site Studio projection is unavailable")
    for asset in projection.get("assets", []):
        copy_asset(files_root, output, asset)
    bundle = build_delivery_bundle(output)
    return {
        "schema": "adaos.site_studio.publication_export.v1",
        "project_id": project_id,
        "site_id": bundle.site_id,
        "revision_id": active_revision,
        "source_digest": source_digest,
        "artifact_digest": artifact_digest,
        "deployment_digest": bundle.deployment_digest,
        "bundle_digest": bundle.manifest["bundle_digest"],
        "artifacts": len(bundle.manifest["artifacts"]),
    }


def read_active_revision(database_path: Path) -> dict[str, Any]:
    """Read the immutable identity used for publication compare-and-swap."""

    database_path = database_path.resolve(strict=True)
    database = sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = database.execute(
            "SELECT project_id,body FROM site_studio_projects ORDER BY project_id"
        ).fetchall()
        if len(rows) != 1:
            raise ValueError("publication requires one retained Site Studio project")
        project_id, raw = rows[0]
        project = json.loads(raw)
        revision_id = str(project.get("active_revision_id") or "")
        source_digest = str(project.get("active_source_digest") or "")
        site_id = str(project.get("site_id") or "")
        row = database.execute(
            "SELECT source_digest,artifact_digest FROM site_studio_revisions "
            "WHERE project_id=? AND revision_id=?",
            (project_id, revision_id),
        ).fetchone()
    finally:
        database.close()
    if (
        not revision_id
        or not site_id
        or not _DIGEST.fullmatch(source_digest)
        or not row
        or row[0] != source_digest
    ):
        raise ValueError("active Site Studio revision identity is invalid")
    return {
        "project_id": str(project_id),
        "site_id": site_id,
        "revision_id": revision_id,
        "source_digest": source_digest,
        "artifact_digest": str(row[1]),
        "preview_deployment_digest": str(
            project.get("preview_deployment_digest") or ""
        ),
    }


def copy_asset(files_root: Path, output: Path, asset: dict[str, Any]) -> None:
    expected = str(asset.get("digest") or "")
    if not _DIGEST.fullmatch(expected):
        raise ValueError("Site Studio asset digest is invalid")
    public_path = str(asset.get("public_path") or "")
    target = admitted_target(output, public_path)
    candidates = list(
        (files_root / "attachments/objects" / expected[7:9]).glob(
            expected[7:] + ".*"
        )
    )
    if (
        len(candidates) != 1
        or not candidates[0].is_file()
        or candidates[0].is_symlink()
    ):
        raise ValueError("owned Site Studio asset blob is unavailable")
    raw = candidates[0].read_bytes()
    if byte_digest(raw) != expected or len(raw) != asset.get("size_bytes"):
        raise ValueError("owned Site Studio asset blob digest mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)


def admitted_target(root: Path, relative: str) -> Path:
    if not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise ValueError("invalid Site Studio publication path")
    target = (root / relative).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Site Studio publication path escapes output")
    return target


def semantic_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return byte_digest(raw)


def byte_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


__all__ = ["export_publication", "read_active_revision"]
