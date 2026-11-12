"""Universal component properties use the same semantic write path as chat."""
from contextlib import closing
import json
from uuid import uuid4

from adaos.sdk.site_studio import component_editor, component_field_operations
from .authoring import _database
from .compiler import current, records
from .discussion import authorize


def query(id="", node_path="", resource="component_fields"):
    authorize()
    if not id:
        return {"items": [], "tree": []}
    node_path = "" if node_path in {"root", "__block__"} else node_path
    with closing(_database()) as db:
        record = next(record for record in records(db, "sections") if record["id"] == id)
        item = component_editor(current(db)["source"], id, revision=record["revision"], node_path=node_path)
    root = dict(item["tree"][0], id="__block__")
    return {"items": [item], "root": root}


def update(id, payload, expected_revision, expected_source_digest, node_path="", resource="component_fields", operation="update"):
    authorize(True)
    if resource != "component_fields" or operation != "update":
        raise ValueError("Unsupported property operation")
    before = query(id, node_path)["items"][0]
    if before["revision"] != expected_revision or before["source_digest"] != expected_source_digest:
        return {"ok": False, "error": "revision_conflict", "message": "Раздел изменился. Обновите свойства; введённый текст сохранён в форме."}
    operations = component_field_operations(before, payload)
    if not operations:
        return {"ok": True, "item": before}
    from .main import propose_site_operations, commit_site_change
    cid = "properties-" + uuid4().hex
    proposed = propose_site_operations(change_set_id=cid, origin="form", operations=operations, rationale="Изменены свойства компонента")
    if not proposed.get("ok"):
        return proposed
    committed = commit_site_change(change_set_id=cid, expected_source_digest=expected_source_digest)
    if not committed.get("ok"):
        return committed
    return {"ok": True, "item": query(id, node_path)["items"][0]}
