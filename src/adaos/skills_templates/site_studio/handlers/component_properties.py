"""Universal component properties use the same semantic write path as chat."""
from contextlib import closing
import json
from uuid import uuid4

from adaos.sdk.site_studio import component_editor, component_field_operations, digest
from .authoring import _database
from .compiler import current, records
from .discussion import authorize


def _editor(source, id, revision, node_path):
    item = component_editor(source, id, revision=revision, node_path=node_path)
    section = next(section for route in source["routes"] for section in route["sections"] if section["id"] == id)
    value = next(resource["data"] for resource in source["resources"] if resource["id"] == item["resource_id"])
    for token in node_path.split(".") if node_path else []:
        value = value[int(token)] if isinstance(value, list) else value[token]
    item["node_digest"] = digest({"section": section, "node_path": node_path, "value": value})
    return item


def query(id="", node_path="", resource="component_fields"):
    authorize()
    if not id:
        return {"items": [], "tree": []}
    node_path = "" if node_path in {"root", "__block__"} else node_path
    with closing(_database()) as db:
        source = current(db)["source"]
        record = next(record for record in records(db, "sections") if record["id"] == id)
        item = _editor(source, id, record["revision"], node_path)
    root = dict(item["tree"][0], id="__block__")
    return {"items": [item], "root": root}


def update(id, payload, expected_revision, expected_source_digest, node_path="", resource="component_fields", operation="update"):
    authorize(True)
    if resource != "component_fields" or operation != "update":
        raise ValueError("Unsupported property operation")
    before = query(id, node_path)["items"][0]
    base = before
    if before["revision"] != expected_revision or before["source_digest"] != expected_source_digest:
        # A collection sibling or another section may have changed since this
        # node was opened. Rebase only when its complete retained value AND
        # section identity are unchanged, never by replacing an old form's CAS.
        with closing(_database()) as db:
            row = db.execute("SELECT source_body FROM site_studio_revisions WHERE source_digest=? LIMIT 1",
                             (expected_source_digest,)).fetchone()
        try:
            base = _editor(json.loads(row[0]), id, expected_revision, before["node_path"]) if row else None
        except (KeyError, IndexError, StopIteration, TypeError, ValueError):
            base = None
        if not base or base["node_digest"] != before["node_digest"] or type(expected_revision) is not int or not 1 <= expected_revision <= before["revision"]:
            return {"ok": False, "error": "revision_conflict", "message": "Этот элемент изменился. Обновите свойства; введённый текст сохранён в форме."}
    operations = component_field_operations(base, payload)
    if not operations:
        return {"ok": True, "item": before}
    from .main import propose_site_operations, commit_site_change
    cid = "properties-" + uuid4().hex
    proposed = propose_site_operations(change_set_id=cid, origin="form", operations=operations, rationale="Изменены свойства компонента")
    if not proposed.get("ok"):
        return proposed
    committed = commit_site_change(change_set_id=cid, expected_source_digest=before["source_digest"])
    if not committed.get("ok"):
        return committed
    return {"ok": True, "item": query(id, node_path)["items"][0]}
