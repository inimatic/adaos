#!/usr/bin/env python3
"""Export the active retained Site Studio revision as a PS3 delivery directory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaos.services.public_site_delivery import build_delivery_bundle  # noqa: E402


DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


def export_publication(database_path: Path, files_root: Path, output: Path) -> dict:
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
            "SELECT body FROM site_studio_artifacts WHERE digest=? AND kind='compiled_site_revision'",
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
    try:
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
    except Exception:
        # Preserve partial output for private diagnosis. Callers always choose a
        # fresh destination, so incomplete content can never be admitted later.
        raise
    receipt = {
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
    return receipt


def copy_asset(files_root: Path, output: Path, asset: dict) -> None:
    expected = str(asset.get("digest") or "")
    if not DIGEST.fullmatch(expected):
        raise ValueError("Site Studio asset digest is invalid")
    public_path = str(asset.get("public_path") or "")
    target = admitted_target(output, public_path)
    candidates = list((files_root / "attachments/objects" / expected[7:9]).glob(expected[7:] + ".*"))
    if len(candidates) != 1 or not candidates[0].is_file() or candidates[0].is_symlink():
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


def semantic_digest(value) -> str:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--files-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = export_publication(args.database, args.files_root, args.output)
    print(json.dumps({"ok": True, "output": str(args.output.resolve()), **receipt}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
