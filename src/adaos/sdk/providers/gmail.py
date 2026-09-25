"""Public typed SDK facade for the governed Google Gmail provider."""

from __future__ import annotations

from concurrent.futures import Future, TimeoutError as FutureTimeoutError
import copy
import json
import logging
import threading
import time
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


_log = logging.getLogger("adaos.sdk.providers.gmail")
_READ_CACHE_TTL_S = {
    "get_message": 5.0,
    "list_labels": 30.0,
    "list_message_summaries": 5.0,
    "list_messages": 5.0,
}
_READ_CACHE_MAX_ENTRIES = 128
_READ_CACHE_LOCK = threading.RLock()
_READ_CACHE: dict[tuple[str, ...], tuple[float, dict[str, Any]]] = {}
_READ_INFLIGHT: dict[tuple[str, ...], Future[dict[str, Any]]] = {}


def _read_cache_key(
    operation: str,
    *,
    application: Mapping[str, Any],
    subject_ref: str,
    account_id: str,
    arguments: Mapping[str, Any] | None,
    permission_profile: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    return (
        str(application.get("application_id") or "").strip(),
        str(application.get("release_digest") or "").strip(),
        subject_ref,
        account_id,
        operation,
        json.dumps(
            dict(arguments or {}),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        json.dumps(
            dict(permission_profile or {}),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


def _invalidate_read_cache(
    *,
    application_id: str,
    subject_ref: str,
    account_id: str,
) -> None:
    prefix = (application_id,)
    with _READ_CACHE_LOCK:
        for key in list(_READ_CACHE):
            if key[:1] == prefix and key[2] == subject_ref and key[3] == account_id:
                _READ_CACHE.pop(key, None)


def _execute_read_single_flight(
    operation: str,
    *,
    key: tuple[str, ...],
    execute: Any,
) -> dict[str, Any]:
    now = time.monotonic()
    owner = False
    with _READ_CACHE_LOCK:
        for stale_key, (expires_at, _value) in list(_READ_CACHE.items()):
            if expires_at <= now:
                _READ_CACHE.pop(stale_key, None)
        cached = _READ_CACHE.get(key)
        if cached is not None:
            _log.debug("gmail read cache hit operation=%s", operation)
            return copy.deepcopy(cached[1])
        future = _READ_INFLIGHT.get(key)
        if future is None:
            future = Future()
            _READ_INFLIGHT[key] = future
            owner = True

    if not owner:
        try:
            result = future.result(timeout=30.0)
        except FutureTimeoutError as exc:
            raise GmailProviderError(
                "gmail_provider_unavailable",
                retryable=True,
            ) from exc
        _log.debug("gmail read single-flight follower operation=%s", operation)
        return copy.deepcopy(result)

    try:
        result = execute()
    except BaseException as exc:
        with _READ_CACHE_LOCK:
            _READ_INFLIGHT.pop(key, None)
            future.set_exception(exc)
        raise

    with _READ_CACHE_LOCK:
        _READ_INFLIGHT.pop(key, None)
        if result.get("ok") is True:
            _READ_CACHE[key] = (
                time.monotonic() + _READ_CACHE_TTL_S[operation],
                copy.deepcopy(result),
            )
            while len(_READ_CACHE) > _READ_CACHE_MAX_ENTRIES:
                _READ_CACHE.pop(next(iter(_READ_CACHE)))
        future.set_result(copy.deepcopy(result))
    return result


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
    application_id = str(application["application_id"])
    permission_profile = _permission_profile(application)

    def execute() -> dict[str, Any]:
        return provider.execute(
            operation,
            application_id=application_id,
            release_digest=str(application["release_digest"]),
            subject_ref=subject_ref,
            account_id=account_id,
            arguments=arguments,
            candidate_permission_profile=permission_profile,
        )

    if operation in _READ_CACHE_TTL_S:
        return _execute_read_single_flight(
            operation,
            key=_read_cache_key(
                operation,
                application=application,
                subject_ref=subject_ref,
                account_id=account_id,
                arguments=arguments,
                permission_profile=permission_profile,
            ),
            execute=execute,
        )
    result = execute()
    if operation in {"modify_message", "send_message", "trash_message"}:
        _invalidate_read_cache(
            application_id=application_id,
            subject_ref=subject_ref,
            account_id=account_id,
        )
    return result


def begin_connection(*, account_id: str = GOOGLE_GMAIL_PROVIDER_ID) -> dict[str, Any]:
    provider, application, subject_ref = _invocation()
    return provider.begin_authorization(
        application_id=str(application["application_id"]),
        release_digest=str(application["release_digest"]),
        subject_ref=subject_ref,
        account_id=account_id,
        candidate_permission_profile=_permission_profile(application),
    )


def reusable_connections(
    *, account_id: str = GOOGLE_GMAIL_PROVIDER_ID
) -> dict[str, Any]:
    provider, application, subject_ref = _invocation()
    return provider.reusable_connections(
        application_id=str(application["application_id"]),
        release_digest=str(application["release_digest"]),
        subject_ref=subject_ref,
        account_id=account_id,
        candidate_permission_profile=_permission_profile(application),
    )


def attach_reusable_connection(
    *, account_id: str = GOOGLE_GMAIL_PROVIDER_ID
) -> dict[str, Any]:
    provider, application, subject_ref = _invocation()
    return provider.attach_reusable_connection(
        application_id=str(application["application_id"]),
        release_digest=str(application["release_digest"]),
        subject_ref=subject_ref,
        account_id=account_id,
        candidate_permission_profile=_permission_profile(application),
    )


def connection_status(*, account_id: str = GOOGLE_GMAIL_PROVIDER_ID) -> dict[str, Any]:
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


def list_message_summaries(
    *,
    query: str = "",
    label_ids: Iterable[str] = (),
    page_token: str = "",
    max_results: int = 20,
    account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
) -> dict[str, Any]:
    """List messages with bounded metadata fetched inside the Core provider.

    Prefer this operation for first-paint collections. It avoids a consumer-side
    sequence of one ``list_messages`` call followed by one ``get_message`` call
    per result while preserving the same Core-owned credential boundary.
    """

    return _execute(
        "list_message_summaries",
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


def list_labels(*, account_id: str = GOOGLE_GMAIL_PROVIDER_ID) -> dict[str, Any]:
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
    "attach_reusable_connection",
    "begin_connection",
    "connection_status",
    "get_message",
    "list_labels",
    "list_message_summaries",
    "list_messages",
    "modify_message",
    "reusable_connections",
    "send_message",
    "trash_message",
]
