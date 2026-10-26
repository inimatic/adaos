"""Typed read-only Drive access. Core retains OAuth credentials and admission."""
from typing import Any
from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.application import current_application
from adaos.services.policy.caller import current_caller
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.providers.google_drive import GoogleDriveProvider, GOOGLE_DRIVE_SKILL_CAPABILITY


def _invocation():
    ctx = require_ctx("sdk.providers.drive")
    require_skill_capability(ctx, GOOGLE_DRIVE_SKILL_CAPABILITY)
    app, caller = current_application(), current_caller()
    if not app or not caller or not app.get("subject_ref"):
        raise PermissionError("drive_provider_application_context_missing")
    if caller.kind == "user" and app["subject_ref"] != caller.ref():
        raise PermissionError("drive_provider_subject_mismatch")
    arguments = {"application_id": str(app["application_id"]), "release_digest": str(app["release_digest"]),
                 "subject_ref": str(app["subject_ref"]), "account_id": "google.drive"}
    if app.get("runtime_source") == "dev" and isinstance(app.get("permission_profile"), dict):
        arguments["candidate_permission_profile"] = dict(app["permission_profile"])
    return GoogleDriveProvider.from_context(ctx), arguments


def begin_connection() -> dict[str, Any]:
    """Return a Core-bound OAuth URL; user completes consent in Google."""
    provider, args = _invocation()
    return provider.begin_authorization(**args)


def connection_status() -> dict[str, Any]:
    """Read account admission. A disconnected account raises a secret-free error."""
    provider, args = _invocation()
    return provider.execute("connection_status", **args)


def list_files(*, parent_id: str = "root", page_token: str = "", page_size: int = 50) -> dict[str, Any]:
    """One folder's child folders/images, including metadata and pagination."""
    provider, args = _invocation()
    return provider.execute("list_files", **args, arguments={"parent_id": parent_id, "page_token": page_token, "page_size": page_size})


def get_file(file_id: str) -> dict[str, Any]:
    provider, args = _invocation()
    return provider.execute("get_file", **args, arguments={"file_id": file_id})


def read_file(file_id: str) -> dict[str, Any]:
    """Read <=64 MiB image bytes for owned import; never returns tokens or URLs."""
    provider, args = _invocation()
    return provider.execute("read_file", **args, arguments={"file_id": file_id})


def read_thumbnail(file_id: str) -> dict[str, Any]:
    """Read <=2 MiB thumbnail bytes for an owned attachment preview."""
    provider, args = _invocation()
    return provider.execute("read_thumbnail", **args, arguments={"file_id": file_id})


__all__ = ["begin_connection", "connection_status", "list_files", "get_file", "read_file", "read_thumbnail"]
