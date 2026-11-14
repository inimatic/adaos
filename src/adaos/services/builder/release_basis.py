"""Pin the actual installed/selected Application, independently of old workflow history."""
from pathlib import Path

from adaos.services.applications.runtime import get_application_service
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.builder.traceability import digest


def capture(state_dir, project_id):
    store = get_application_service(Path(state_dir)).store
    applications = [app for app in store.list_applications() if app.legacy_project_id == project_id]
    if len(applications) > 1:
        raise ValueError("Implementation basis requires an unambiguous Project Application")
    payload = {"schema": "adaos.builder.implementation_basis.v1", "project_id": project_id,
               "application_id": None, "installation": None, "channels": {}, "selections": []}
    if applications:
        app = applications[0]
        payload["application_id"] = app.application_id
        installation = store.get_installation(app.application_id)
        if installation:
            payload["installation"] = {"release_digest": installation.installed_release_digest,
                                       "status": installation.status, "revision": installation.revision}
        payload["channels"] = store.get_channels(app.application_id).get("channels") or {}
        payload["selections"] = sorted([row.to_dict() for row in store.list_runtime_selections()
                                         if row.application_id == app.application_id], key=lambda row: row["webspace_id"])
    identity = digest(payload)
    atomic_write_json(Path(state_dir) / "builder/implementation-bases" / f"{identity[7:]}.json", payload)
    return {"ref": "implementation_basis:" + identity[7:], "digest": identity, "basis": payload}


def require_current(state_dir, reference):
    import json
    prefix, _, key = str(reference).partition(":")
    if prefix != "implementation_basis" or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Invalid implementation basis reference")
    path = Path(state_dir) / "builder/implementation-bases" / f"{key}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if digest(payload) != "sha256:" + key:
        raise ValueError("Implementation basis integrity mismatch")
    current = capture(state_dir, payload["project_id"])
    if current["basis"] != payload:
        raise ValueError("Application implementation basis changed; rebase the Change before Trial preparation")
    return current


def references(change):
    return [ref for run in change.get("runs", []) if run.get("activity") == "plan_change_set"
            for ref in run.get("input_refs", []) if str(ref).startswith("implementation_basis:")]


def require_change_basis(state_dir, change):
    for reference in references(change):
        require_current(state_dir, reference)
