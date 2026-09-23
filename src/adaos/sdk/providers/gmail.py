"""Public typed SDK facade for the governed Google Gmail provider."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from adaos.sdk.core._ctx import require_ctx
from adaos.services.policy.application import current_application
from adaos.services.policy.caller import current_caller
from adaos.services.policy.skill_capabilities import require_skill_capability
from adaos.services.providers.google_gmail import (
    GOOGLE_GMAIL_PROVIDER_ID,
    GOOGLE_GMAIL_SKILL_CAPABILITY,
    GoogleGmailProvider,
    GoogleGmailProviderError as GmailProviderError,
)


def _invocation() -> tuple[GoogleGmailProvider, dict[str, Any], str]:
    ctx = require_ctx("sdk.providers.gmail")
    require_skill_capability(ctx, GOOGLE_GMAIL_SKILL_CAPABILITY)
    application = current_application()
    caller = current_caller()
    if application is None or caller is None:
        raise PermissionError("gmail_provider_application_context_missing")
    subject_ref = str(application.get("subject_ref") or "").strip()
    if not subject_ref:
        raise PermissionError("gmail_provider_subject_missing")
    # A browser invocation is authenticated as a session while trusted tool
    # ingress resolves and binds its owning user as application.subject_ref.
    # Only a direct user caller can therefore be compared byte-for-byte here.
    if caller.kind == "user" and subject_ref != caller.ref():
        raise PermissionError("gmail_provider_subject_mismatch")
    return GoogleGmailProvider.from_context(ctx), application, subject_ref


def _permission_profile(application: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return only the Core-verified DEV profile, never tool arguments."""

    if str(application.get("runtime_source") or "") != "dev":
        return None
    profile = application.get("permission_profile")
    return dict(profile) if isinstance(profile, Mapping) else None


def _execute(
    operation: str,
    *,
    account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    provider, application, subject_ref = _invocation()
    return provider.execute(
        operation,
        application_id=str(application["application_id"]),
        release_digest=str(application["release_digest"]),
        subject_ref=subject_ref,
        account_id=account_id,
        arguments=arguments,
        candidate_permission_profile=_permission_profile(application),
    )


def begin_connection(
    *, account_id: str = GOOGLE_GMAIL_PROVIDER_ID
) -> dict[str, Any]:
    provider, application, subject_ref = _invocation()
    return provider.begin_authorization(
        application_id=str(application["application_id"]),
        release_digest=str(application["release_digest"]),
        subject_ref=subject_ref,
        account_id=account_id,
        candidate_permission_profile=_permission_profile(application),
    )


def connection_status(
    *, account_id: str = GOOGLE_GMAIL_PROVIDER_ID
) -> dict[str, Any]:
    return _execute("connection_status", account_id=account_id)


def list_messages(
    *,
    query: str = "",
    label_ids: Iterable[str] = (),
    page_token: str = "",
    max_results: int = 50,
    account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
) -> dict[str, Any]:
    return _execute(
        "list_messages",
        account_id=account_id,
        arguments={
            "query": query,
            "label_ids": list(label_ids),
            "page_token": page_token,
            "max_results": max_results,
        },
    )


def get_message(
    message_id: str,
    *,
    format: str = "full",
    account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
) -> dict[str, Any]:
    return _execute(
        "get_message",
        account_id=account_id,
        arguments={"message_id": message_id, "format": format},
    )


def list_labels(
    *, account_id: str = GOOGLE_GMAIL_PROVIDER_ID
) -> dict[str, Any]:
    return _execute("list_labels", account_id=account_id)


def modify_message(
    message_id: str,
    *,
    add_label_ids: Iterable[str] = (),
    remove_label_ids: Iterable[str] = (),
    account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
) -> dict[str, Any]:
    return _execute(
        "modify_message",
        account_id=account_id,
        arguments={
            "message_id": message_id,
            "add_label_ids": list(add_label_ids),
            "remove_label_ids": list(remove_label_ids),
        },
    )


def trash_message(
    message_id: str,
    *,
    account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
) -> dict[str, Any]:
    return _execute(
        "trash_message",
        account_id=account_id,
        arguments={"message_id": message_id},
    )


def send_message(
    raw: str,
    *,
    idempotency_key: str,
    account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
) -> dict[str, Any]:
    return _execute(
        "send_message",
        account_id=account_id,
        arguments={"raw": raw, "idempotency_key": idempotency_key},
    )


__all__ = [
    "GmailProviderError",
    "begin_connection",
    "connection_status",
    "get_message",
    "list_labels",
    "list_messages",
    "modify_message",
    "send_message",
    "trash_message",
]
