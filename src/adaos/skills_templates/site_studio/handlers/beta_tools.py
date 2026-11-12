"""Additional Site Studio beta tools; all share the application's project guard."""
from contextlib import closing
import json

from adaos.sdk.core.decorators import tool
from .projects import project_bound
from .authoring import _database
from .discussion import authorize


@tool(summary="List private source files belonging to the selected site.", side_effects="read_only")
@project_bound
def query_source_files(id: str = "", resource: str = "source_files"):
    from .source_files import query
    return query(id)


@tool(summary="Create, update or delete one private source file with revision checks.", side_effects="local_write")
@project_bound(write=True)
def mutate_source_files(operation: str, payload: dict | None = None, id: str = "", expected_revision: int | None = None, confirmed: bool = False, resource: str = "source_files"):
    from .source_files import mutate
    return mutate(operation, payload, id, expected_revision, confirmed)


@tool(summary="Admit one verified private project source upload.", side_effects="local_write")
@project_bound(write=True)
def upload_source_file(filename: str, field_id: str, media_type: str, size_bytes: int, digest: str, webspace_id: str = ""):
    from .source_files import upload
    return upload(filename, field_id, media_type, size_bytes, digest, webspace_id)


@tool(summary="Authorize access to an admitted project source attachment.", side_effects="read_only")
@project_bound
def read_source_attachment(ref: str, field_id: str = "", webspace_id: str = ""):
    from .source_files import read_upload
    return read_upload(ref, field_id, webspace_id)


@tool(summary="Read a bounded range of a private project source at its expected version.", side_effects="read_only")
@project_bound
def read_source_material(id: str, expected_revision: int, offset: int = 0, expected_digest: str = ""):
    from .source_files import read
    return read(id, expected_revision, offset, expected_digest)


@tool(summary="List canonical source and media references available to this site's chat.", side_effects="read_only")
@project_bound
def query_chat_references():
    from .authoring_chat import reference_inventory
    authorize()
    with closing(_database()) as db:
        return {"items": reference_inventory(db)}


@tool(summary="Read typed editable properties and nested items of one component.", side_effects="read_only")
@project_bound
def query_component_fields(id: str = "", node_path: str = "", resource: str = "component_fields"):
    from .component_properties import query
    return query(id, node_path, resource)


@tool(summary="Save displayed typed component properties against their exact source revision.", side_effects="local_write")
@project_bound(write=True)
def update_component_fields(id: str, payload: dict, expected_revision: int, expected_source_digest: str,
                            node_path: str = "", resource: str = "component_fields", operation: str = "update"):
    from .component_properties import update
    return update(id, payload, expected_revision, expected_source_digest, node_path, resource, operation)


@tool(summary="Stop an owned pending chat-authoring request without changing the site.", side_effects="local_write")
@project_bound(write=True)
def cancel_authoring(job_id: str):
    from .authoring_chat import cancel
    return cancel(job_id)


@tool(summary="Continue the selected site's owned pending authoring request; never start a new user request.", side_effects="local_write")
@project_bound(write=True)
def continue_authoring():
    from .authoring_chat import _ensure, resume
    from .compiler import encode
    actor = authorize(True)
    with closing(_database()) as db:
        _ensure(db)
        row = db.execute("SELECT id FROM site_studio_chat_jobs WHERE actor=? AND status NOT IN ('completed','failed','cancelled') ORDER BY rowid LIMIT 1", (encode(actor),)).fetchone()
    return resume(row[0]) if row else {"ok": True, "status": "idle"}


@tool(summary="Undo the most recent committed change against its exact current source.", side_effects="local_write")
@project_bound(write=True)
def undo_site_change(change_set_id: str, expected_source_digest: str):
    from uuid import uuid4
    from .compiler import current, project_identity, _derive_operations
    from .main import propose_site_operations, commit_site_change
    from adaos.sdk.site_studio import change_set_history
    authorize(True)
    with closing(_database()) as db:
        pid = project_identity(db)[0]
        preview = current(db)
        change = next(item for item in change_set_history(db, pid) if item["change_set_id"] == change_set_id)
        if (preview["source_digest"] != expected_source_digest or
                (change.get("commit_receipt") or {}).get("source_digest") != expected_source_digest):
            return {"ok": False, "error": "revision_conflict", "message": "После этой правки сайт изменился. Обновите историю и отмените последнюю правку."}
        row = db.execute("SELECT source_body FROM site_studio_revisions WHERE project_id=? AND revision_id=?", (pid, change["base_revision_id"])).fetchone()
        if not row:
            raise ValueError("The predecessor source is unavailable")
        operations = _derive_operations(preview["source"], json.loads(row[0]))
    cid = "undo-" + uuid4().hex
    propose_site_operations(change_set_id=cid, origin="form", operations=operations, rationale="Отменено изменение " + change_set_id)
    return commit_site_change(change_set_id=cid, expected_source_digest=expected_source_digest)
