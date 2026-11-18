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


def delivery_snapshot():
    """Read Hub source policy and Core rollout identities for Management."""
    return snapshot(section="delivery")


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


def plan_migration(role, expected_revision, desired_path=None):
    _authorize(write=True)
    from adaos.sdk.system import _post_local_admin
    return _post_local_admin("/api/admin/maintenance/migrations/plan", {
        "role": role, "expected_revision": expected_revision, "desired_path": desired_path})


def start_migration(plan_digest):
    _authorize(write=True)
    from adaos.sdk.system import _post_local_admin
    return _post_local_admin("/api/admin/maintenance/migrations/start", {"plan_digest": plan_digest})


def control_migration(job_id, action, plan_digest=None):
    _authorize(write=action != "status")
    from .system import _post_local_admin
    from_context().migrations._path(job_id)
    if action == "status":
        return next((row for row in from_context().migrations.snapshot() if row["job_id"] == job_id), {})
    if action in {"cleanup_review", "cleanup_apply", "cleanup_resume"}:
        operation = action.removeprefix("cleanup_")
        return _post_local_admin(f"/api/admin/maintenance/migrations/{job_id}/retirement/{operation}",
                                 {"plan_digest": plan_digest} if operation == "apply" else {})
    if action not in {"resume", "cancel"}:
        raise ValueError("Unknown migration action")
    return _post_local_admin(f"/api/admin/maintenance/migrations/{job_id}/{action}", {})
