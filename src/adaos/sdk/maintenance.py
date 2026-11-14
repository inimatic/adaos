"""Management facade for node-owned maintenance; no Skill filesystem effects."""
from adaos.sdk import access
from adaos.services.node_maintenance import from_context


def _authorize(write=False):
    access.require("workspace.write" if write else "workspace.read")
    if access.application():
        access.require_app_capability("desktop.settings.write")
    else:
        from adaos.services.personalization_runtime import current_user_id
        caller = access.current_user()
        if not caller or caller["id"] != current_user_id():
            raise PermissionError("Node owner authority is required")


def snapshot(section="storage"):
    _authorize()
    return from_context().snapshot(section=section)


def plan_location(role, desired_path, expected_revision, quota_bytes=None, min_free_bytes=20*1024**3):
    _authorize(write=True)
    return from_context().locations.plan(role=role, desired_path=desired_path, expected_revision=expected_revision,
        quota_bytes=quota_bytes, min_free_bytes=min_free_bytes)


def apply_location(plan_digest):
    _authorize(write=True)
    return from_context().locations.apply(plan_digest)


def advance_inventory(role, restart=False):
    _authorize(write=True)
    if role not in from_context().locations.defaults():
        raise ValueError("Unknown storage role")
    from adaos.sdk.system import _post_local_admin
    return _post_local_admin(f"/api/admin/maintenance/inventory/{role}?restart={'true' if restart else 'false'}", {})


def plan_testing(suite_id, node_id, target_build=""):
    _authorize(write=True)
    from adaos.sdk.system import _post_local_admin
    return _post_local_admin("/api/admin/maintenance/testing/plan", {
        "suite_id": suite_id, "node_id": node_id, "target_build": target_build})


def apply_testing(plan_digest):
    _authorize(write=True)
    from adaos.sdk.system import _post_local_admin
    return _post_local_admin("/api/admin/maintenance/testing/apply", {"plan_digest": plan_digest})
