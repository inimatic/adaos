"""Private project source files over the existing immutable material store."""
from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
from uuid import uuid4

from .authoring import intake_material, read_material_object
from .contracts import canonical_json

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PROJECT_BYTES = 64 * 1024 * 1024


def _ensure(db):
    db.execute("""CREATE TABLE IF NOT EXISTS site_studio_source_files(
        project_id TEXT NOT NULL,id TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,
        PRIMARY KEY(project_id,id))""")


def source_files(db, project_id: str):
    _ensure(db)
    return [json.loads(row[0]) for row in db.execute(
        "SELECT body FROM site_studio_source_files WHERE project_id=? ORDER BY id LIMIT 1000", (project_id,))
        if not json.loads(row[0]).get("deleted")]


def _current(db, project_id, identity, expected_revision):
    _ensure(db)
    row = db.execute("SELECT revision,body FROM site_studio_source_files WHERE project_id=? AND id=?", (project_id, identity)).fetchone()
    if not row or json.loads(row[1]).get("deleted"):
        raise ValueError("Source material is not available in this project")
    if type(expected_revision) is not int or row[0] != expected_revision:
        raise ValueError("revision_conflict: source material changed; reload before retrying")
    return json.loads(row[1])


def save_source_file(db, *, project_id, title, folder="", role="orientation", note="", content=None,
                     media_type="text/plain", identity="", expected_revision=None):
    _ensure(db)
    title = str(title).strip()
    folder = str(folder).strip().rstrip("/")
    logical = PurePosixPath(folder) / title
    if (not title or len(title) > 180 or len(folder) > 400 or "\\" in str(logical) or
            any(part in {".", ".."} for part in str(logical).split("/")) or "/" in title or
            folder.startswith("/") or re.search(r"[\x00-\x1f:]", str(logical))):
        raise ValueError("A bounded relative source filename and folder are required")
    if role not in {"orientation", "authoritative"} or not isinstance(note, str) or len(note) > 8000:
        raise ValueError("Invalid source material role or note")
    previous = _current(db, project_id, identity, expected_revision) if identity else None
    identity = identity or "source-" + uuid4().hex[:24]
    revision = previous["revision"] + 1 if previous else 1
    files = source_files(db, project_id)
    if any(item["id"] != identity and item["path"] == str(logical) for item in files):
        raise ValueError("A source with this path already exists")
    item = dict(previous or {}, id=identity, revision=revision, title=title, label=title,
                folder=folder, path=str(logical), role=role, note=note, kind="source")
    if content is not None:
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_FILE_BYTES:
            raise ValueError("Source files must contain 1 byte to 10 MiB")
        if sum(value["size_bytes"] for value in files if value["id"] != identity) + len(content) > MAX_PROJECT_BYTES:
            raise ValueError("Source files exceed the 64 MiB project capacity")
        retained = intake_material(db, project_id=project_id, material_id=identity + "-v" + str(revision),
            role=role, media_type=media_type, content=content, provenance="Owner uploaded project source: " + str(logical),
            rights="Private project reference supplied by its owner", public_eligible=False)
        item.update(material_id=retained["material_id"], object_ref=retained["object_ref"], digest=retained["content_digest"],
                    media_type=media_type, size_bytes=len(content))
    elif not previous:
        raise ValueError("Upload a source file before saving")
    item["uri"] = f"adaos-material://{project_id}/{identity}?revision={revision}"
    db.execute("INSERT INTO site_studio_source_files VALUES(?,?,?,?) ON CONFLICT(project_id,id) DO UPDATE SET revision=excluded.revision,body=excluded.body",
               (project_id, identity, revision, canonical_json(item)))
    return item


def delete_source_file(db, *, project_id, identity, expected_revision):
    item = _current(db, project_id, identity, expected_revision)
    item.update(deleted=True, revision=item["revision"] + 1)
    db.execute("UPDATE site_studio_source_files SET revision=?,body=? WHERE project_id=? AND id=?",
               (item["revision"], canonical_json(item), project_id, identity))
    return item


def read_source_file(db, *, project_id, identity, expected_revision, expected_digest=None, offset=0, length=16000):
    item = _current(db, project_id, identity, expected_revision)
    if expected_digest and expected_digest != item["digest"]:
        raise ValueError("revision_conflict: source content was replaced")
    if type(offset) is not int or offset < 0 or type(length) is not int or not 1 <= length <= 24000:
        raise ValueError("Invalid bounded material read range")
    value = read_material_object(db, item["object_ref"])
    text_type = item["media_type"].startswith("text/") or item["media_type"] in {"application/json", "application/javascript", "image/svg+xml"}
    result = {**item, "offset": offset, "text_available": text_type}
    if text_type:
        text = value["content"].decode("utf-8-sig", errors="replace")
        result.update(content=text[offset:offset + length], total_characters=len(text), has_more=offset + length < len(text))
    else:
        result.update(content="", limitation="Text extraction is unavailable for this file type")
    return result
