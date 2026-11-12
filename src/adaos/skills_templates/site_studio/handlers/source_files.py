"""Project-authorized private sources using Core upload admission and material storage."""
from contextlib import closing
import json
import re
from urllib.parse import unquote

from adaos.sdk import access
from adaos.sdk.data import blob
from adaos.sdk.site_studio import source_files, save_source_file, read_source_file, delete_source_file
from .authoring import _database
from .compiler import project_identity
from .discussion import authorize, SKILL

STORE = "source-files"
LIMIT = 10 * 1024 * 1024


def _ensure(db):
    db.execute("CREATE TABLE IF NOT EXISTS source_uploads(digest TEXT PRIMARY KEY,body TEXT NOT NULL)")


def upload(filename, field_id, media_type, size_bytes, digest, webspace_id=""):
    authorize(True)
    if field_id != "file_ref" or type(size_bytes) is not int or not 0 < size_bytes <= LIMIT:
        raise ValueError("A source file of at most 10 MiB is required")
    if not re.fullmatch(r"[^/\\:\x00-\x1f]{1,180}", filename) or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("Invalid source filename or digest")
    from .service_admission import authorize as authorize_service
    authorize_service("asset.upload_bytes", amount=size_bytes, resource_id=digest)
    receipt = blob.put_upload(STORE)
    if receipt.get("digest") != digest or receipt.get("size_bytes") != size_bytes:
        raise ValueError("Source upload receipt mismatch")
    metadata = {"digest": digest, "filename": filename, "media_type": media_type, "size_bytes": size_bytes}
    with closing(_database()) as db, db:
        _ensure(db)
        db.execute("INSERT OR IGNORE INTO source_uploads VALUES(?,?)", (digest, json.dumps(metadata)))
    return receipt


def _uploaded(db, reference):
    _ensure(db)
    value = str(reference or "")
    prefix = "/api/tools/" + SKILL + "/read_source_attachment/attachments/" + STORE + "/"
    match = re.fullmatch(re.escape(prefix) + r"([a-f0-9]{64})/((?:[A-Za-z0-9_.~-]|%[0-9A-Fa-f]{2}){1,1800})"
                         + r"(?:\?webspace_id=[A-Za-z0-9_.-]{1,128})?", value)
    if not match:
        raise ValueError("Invalid source attachment reference")
    filename = unquote(match[2], errors="strict")
    if filename in {".", ".."} or not re.fullmatch(r"[^/\\:%\x00-\x1f\x7f]{1,180}", filename):
        raise ValueError("Invalid source attachment filename")
    digest = "sha256:" + match[1]
    row = db.execute("SELECT body FROM source_uploads WHERE digest=?", (digest,)).fetchone()
    if not row:
        raise ValueError("Source upload has not been admitted for this project")
    return json.loads(row[0])


def read_upload(ref, field_id="", webspace_id=""):
    authorize()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", ref):
        raise ValueError("Invalid source digest")
    with closing(_database()) as db:
        _ensure(db)
        if not db.execute("SELECT 1 FROM source_uploads WHERE digest=?", (ref,)).fetchone():
            raise ValueError("Source upload is unavailable in this project")
    blob.read_bytes(ref, STORE, max_bytes=LIMIT)
    return {"ok": True, "ref": ref}


def query(id=""):
    authorize()
    with closing(_database()) as db:
        items = source_files(db, project_identity(db)[0])
        for item in items:
            item["name"] = item["title"]
            item["subtitle"] = f"{item['media_type']} · {item['size_bytes']} B"
        if id:
            items = [item for item in items if item["id"] == id]
            if items:
                item = items[0]
                item.update(read_source_file(db, project_id=project_identity(db)[0], identity=id, expected_revision=item["revision"]))
        return {"items": items}


def mutate(operation, payload=None, id="", expected_revision=None, confirmed=False):
    authorize(True)
    payload = dict(payload or {})
    if operation not in {"create", "update", "delete"} or set(payload) - {"title", "folder", "role", "note", "file_ref"}:
        raise ValueError("Unsupported source material operation")
    with closing(_database()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        pid = project_identity(db)[0]
        if operation == "delete":
            if not confirmed:
                raise ValueError("Confirm source material deletion")
            item = delete_source_file(db, project_id=pid, identity=id, expected_revision=expected_revision)
        else:
            previous = next((item for item in source_files(db, pid) if item["id"] == id), None) if id else None
            content = None
            media_type = previous["media_type"] if previous else "text/plain"
            attachment = payload.pop("file_ref", "")
            if attachment and attachment != (previous or {}).get("file_ref"):
                metadata = _uploaded(db, attachment)
                content = blob.read_bytes(metadata["digest"], STORE, max_bytes=LIMIT)
                media_type = metadata["media_type"]
                payload.setdefault("title", metadata["filename"])
            item = save_source_file(db, project_id=pid, identity=id if operation == "update" else "",
                expected_revision=expected_revision, content=content, media_type=media_type, **payload)
            item["file_ref"] = attachment or (previous or {}).get("file_ref", "")
            db.execute("UPDATE site_studio_source_files SET body=? WHERE project_id=? AND id=?", (json.dumps(item), pid, item["id"]))
    return {"ok": True, "item": item}


def read(id, expected_revision, offset=0, expected_digest=""):
    authorize()
    with closing(_database()) as db:
        return {"item": read_source_file(db, project_id=project_identity(db)[0], identity=id,
            expected_revision=expected_revision, expected_digest=expected_digest or None, offset=offset)}
