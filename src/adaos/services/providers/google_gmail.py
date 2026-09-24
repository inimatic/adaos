"""Governed Google Gmail OAuth and REST provider.

The adapter is deliberately narrower than a generic HTTP client.  Application
skills never receive OAuth tokens, client secrets, arbitrary destinations, or
arbitrary HTTP methods.  A provider-level vault record can be attached to more
than one Application without copying credentials into either package.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy as email_policy
from email.parser import BytesParser
import hashlib
import json
import os
import re
import secrets
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote, urlencode

import requests

from adaos.services.applications import (
    ApplicationAccessManagementService,
    get_application_service,
)


GOOGLE_GMAIL_PROVIDER_ID = "google.gmail"
GOOGLE_GMAIL_SKILL_CAPABILITY = "providers.google.gmail"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GMAIL_API_ORIGIN = "https://gmail.googleapis.com"
DEFAULT_CALLBACK_PATH = "/api/providers/google/gmail/oauth/callback"

_TOKEN_TYPE = "Bearer"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_SEND_BYTES = 5 * 1024 * 1024
_MAX_BATCH_MESSAGES = 20
_OAUTH_STATE_TTL_S = 10 * 60
_REFRESH_SKEW_S = 60
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.@+-]{1,180}$")
_MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_LABEL_ID = re.compile(r"^[A-Za-z0-9_./-]{1,256}$")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_OPERATIONS = frozenset(
    {
        "connection_status",
        "get_message",
        "list_labels",
        "list_message_summaries",
        "list_messages",
        "modify_message",
        "send_message",
        "trash_message",
    }
)


class GoogleGmailProviderError(RuntimeError):
    """A secret-free provider error suitable for an Application response."""

    def __init__(
        self,
        code: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(str(code or "gmail_provider_error"))
        self.code = str(code or "gmail_provider_error")
        self.status_code = status_code
        self.retryable = bool(retryable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.code,
            "status_code": self.status_code,
            "retryable": self.retryable,
        }


class HttpTransport(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class GmailOperationResult:
    operation: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
            "operation": self.operation,
            "result": json.loads(json.dumps(dict(self.payload), ensure_ascii=False)),
        }


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utc_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat()


def _safe_json(response: Any, *, error_code: str) -> dict[str, Any]:
    content = getattr(response, "content", b"")
    if isinstance(content, bytes) and len(content) > _MAX_RESPONSE_BYTES:
        raise GoogleGmailProviderError("gmail_response_too_large")
    try:
        payload = response.json()
    except Exception as exc:
        raise GoogleGmailProviderError(error_code) from exc
    if not isinstance(payload, Mapping):
        raise GoogleGmailProviderError(error_code)
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(serialized) > _MAX_RESPONSE_BYTES:
        raise GoogleGmailProviderError("gmail_response_too_large")
    return dict(payload)


def _response_header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return ""
    target = str(name or "").strip().lower()
    return next(
        (
            _text(value)
            for key, value in headers.items()
            if str(key).strip().lower() == target
        ),
        "",
    )


def _gmail_metadata_batch_body(message_ids: list[str], boundary: str) -> bytes:
    lines: list[str] = []
    metadata_query = urlencode(
        [
            ("format", "metadata"),
            ("metadataHeaders", "From"),
            ("metadataHeaders", "To"),
            ("metadataHeaders", "Subject"),
            ("metadataHeaders", "Date"),
        ]
    )
    for index, message_id in enumerate(message_ids):
        encoded_id = quote(message_id, safe="")
        lines.extend(
            [
                f"--{boundary}",
                "Content-Type: application/http",
                f"Content-ID: <adaos-message-{index}>",
                "",
                f"GET /gmail/v1/users/me/messages/{encoded_id}?{metadata_query} HTTP/1.1",
                "Accept: application/json",
                "",
            ]
        )
    lines.extend([f"--{boundary}--", ""])
    return "\r\n".join(lines).encode("ascii")


def _gmail_metadata_batch_payloads(
    response: Any,
    *,
    requested_message_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    content = getattr(response, "content", b"")
    if not isinstance(content, bytes) or not content:
        raise GoogleGmailProviderError("gmail_batch_response_invalid")
    if len(content) > _MAX_RESPONSE_BYTES:
        raise GoogleGmailProviderError("gmail_response_too_large")
    content_type = _response_header(response, "Content-Type")
    if "multipart/" not in content_type.lower():
        raise GoogleGmailProviderError("gmail_batch_response_invalid")
    try:
        envelope = BytesParser(policy=email_policy.default).parsebytes(
            (
                f"Content-Type: {content_type}\r\n"
                "MIME-Version: 1.0\r\n\r\n"
            ).encode("ascii")
            + content
        )
        parts = list(envelope.iter_parts())
    except Exception as exc:
        raise GoogleGmailProviderError("gmail_batch_response_invalid") from exc
    if not parts:
        raise GoogleGmailProviderError("gmail_batch_response_invalid")
    messages_by_id: dict[str, dict[str, Any]] = {}
    omitted_ids: list[str] = []
    for part_index, part in enumerate(parts):
        response_content_id = _text(part.get("Content-ID"))
        content_id_match = re.search(
            r"adaos-message-(\d+)", response_content_id
        )
        requested_index = (
            int(content_id_match.group(1))
            if content_id_match is not None
            else part_index
        )
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raw_payload = part.get_payload()
            if not isinstance(raw_payload, str):
                raise GoogleGmailProviderError("gmail_batch_response_invalid")
            payload = raw_payload.encode("utf-8")
        status_and_headers, separator, json_body = payload.partition(b"\r\n\r\n")
        if not separator:
            status_and_headers, separator, json_body = payload.partition(b"\n\n")
        if not separator:
            raise GoogleGmailProviderError("gmail_batch_response_invalid")
        status_line = status_and_headers.splitlines()[0].decode(
            "ascii", errors="replace"
        )
        match = re.match(r"^HTTP/\S+\s+(\d{3})(?:\s|$)", status_line)
        if match is None:
            raise GoogleGmailProviderError("gmail_batch_response_invalid")
        status = int(match.group(1))
        try:
            item = json.loads(json_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GoogleGmailProviderError("gmail_batch_response_invalid") from exc
        if status == 404:
            missing_id = (
                requested_message_ids[requested_index]
                if requested_index < len(requested_message_ids)
                else ""
            )
            if missing_id:
                omitted_ids.append(missing_id)
            continue
        if status == 401:
            raise GoogleGmailProviderError(
                "gmail_reconnect_required", status_code=401
            )
        if status == 403:
            raise GoogleGmailProviderError(
                "gmail_permission_denied", status_code=403
            )
        if status == 429 or status >= 500:
            raise GoogleGmailProviderError(
                "gmail_provider_unavailable", status_code=status, retryable=True
            )
        if not 200 <= status < 300 or not isinstance(item, Mapping):
            raise GoogleGmailProviderError(
                "gmail_request_failed", status_code=status
            )
        message_id = _text(item.get("id"))
        if not message_id:
            raise GoogleGmailProviderError("gmail_batch_response_invalid")
        messages_by_id[message_id] = dict(item)
    messages = [
        messages_by_id[message_id]
        for message_id in requested_message_ids
        if message_id in messages_by_id
    ]
    return messages, omitted_ids


class GoogleGmailProvider:
    """Core adapter for one fixed Google provider and bounded operation set."""

    def __init__(
        self,
        *,
        vault: Any,
        applications: Any,
        transport: HttpTransport = requests,
        client_id: str = "",
        client_secret: str = "",
        redirect_uri: str = "",
        clock: Callable[[], float] = time.time,
        oauth_configuration_loaded: bool = True,
    ) -> None:
        self.vault = vault
        self.applications = applications
        self.access = ApplicationAccessManagementService(applications)
        self.transport = transport
        self.client_id = _text(client_id)
        self.client_secret = _text(client_secret)
        self.redirect_uri = _text(redirect_uri)
        self.clock = clock
        self._oauth_configuration_loaded = bool(oauth_configuration_loaded)

    @classmethod
    def from_context(
        cls,
        ctx: Any,
        *,
        transport: HttpTransport = requests,
        clock: Callable[[], float] = time.time,
    ) -> "GoogleGmailProvider":
        vault = getattr(ctx, "credential_vault", None)
        if vault is None:
            raise GoogleGmailProviderError("credential_vault_unavailable")
        authority = getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir()
        applications = get_application_service(authority)
        # Windows Keyring access is comparatively expensive. Most provider
        # operations, including connection_status, do not need the OAuth
        # client configuration. Keep environment lookup eager (it is cheap),
        # but defer vault reads until an authorization or refresh operation.
        client_id = _text(os.getenv("ADAOS_GOOGLE_OAUTH_CLIENT_ID"))
        client_secret = _text(os.getenv("ADAOS_GOOGLE_OAUTH_CLIENT_SECRET"))
        base = _text(os.getenv("ADAOS_SELF_BASE_URL"))
        if not base:
            config = getattr(ctx, "config", None)
            base = _text(getattr(config, "local_api_url", ""))
        if not base:
            base = "http://127.0.0.1:8777"
        redirect_uri = base.rstrip("/") + DEFAULT_CALLBACK_PATH
        return cls(
            vault=vault,
            applications=applications,
            transport=transport,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            clock=clock,
            oauth_configuration_loaded=False,
        )

    def _ensure_oauth_configuration(self) -> None:
        if self._oauth_configuration_loaded:
            return
        if not self.client_id:
            self.client_id = _text(
                self.vault.get("provider:google.oauth:client_id", default=None)
            )
        if not self.client_secret:
            self.client_secret = _text(
                self.vault.get("provider:google.oauth:client_secret", default=None)
            )
        self._oauth_configuration_loaded = True

    @staticmethod
    def _state_key(state: str) -> str:
        digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
        return f"provider:{GOOGLE_GMAIL_PROVIDER_ID}:oauth-state:{digest}"

    @staticmethod
    def _account_key(subject_ref: str, account_id: str) -> str:
        identity = f"{GOOGLE_GMAIL_PROVIDER_ID}\0{subject_ref}\0{account_id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"provider:{GOOGLE_GMAIL_PROVIDER_ID}:account:{digest}"

    def _vault_get_json(self, key: str) -> dict[str, Any] | None:
        try:
            raw = self.vault.get(key, default=None)
        except Exception as exc:
            raise GoogleGmailProviderError("credential_vault_read_failed") from exc
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise GoogleGmailProviderError("provider_credential_invalid") from exc
        if not isinstance(value, Mapping):
            raise GoogleGmailProviderError("provider_credential_invalid")
        return dict(value)

    def _vault_put_json(self, key: str, value: Mapping[str, Any]) -> None:
        try:
            self.vault.put(
                key,
                json.dumps(dict(value), ensure_ascii=False, separators=(",", ":")),
                meta={"provider_id": GOOGLE_GMAIL_PROVIDER_ID},
            )
        except Exception as exc:
            raise GoogleGmailProviderError("credential_vault_write_failed") from exc

    def _vault_delete(self, key: str) -> None:
        try:
            self.vault.delete(key)
        except Exception as exc:
            raise GoogleGmailProviderError("credential_vault_write_failed") from exc

    def _provider_declaration(
        self,
        application_id: str,
        release_digest: str,
        *,
        candidate_permission_profile: Mapping[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        release = None
        if candidate_permission_profile is None:
            try:
                release = self.applications.store.get_release(
                    application_id, release_digest
                )
            except Exception as exc:
                raise GoogleGmailProviderError(
                    "application_release_unavailable"
                ) from exc
            declarations = {
                _text(item.get("id")).lower(): dict(item)
                for item in release.permission_profile.external_providers
                if isinstance(item, Mapping)
            }
        else:
            try:
                from adaos.domain.application_access import ApplicationPermissionProfile

                profile = ApplicationPermissionProfile.from_mapping(
                    candidate_permission_profile
                )
            except Exception as exc:
                raise GoogleGmailProviderError(
                    "gmail_permission_profile_invalid"
                ) from exc
            declarations = {
                _text(item.get("id")).lower(): dict(item)
                for item in profile.external_providers
                if isinstance(item, Mapping)
            }
        declaration = declarations.get(GOOGLE_GMAIL_PROVIDER_ID)
        if declaration is None:
            raise GoogleGmailProviderError("gmail_provider_not_declared")
        scopes = {
            _text(item)
            for item in declaration.get("scopes") or ()
            if _text(item)
        }
        if scopes != {GMAIL_MODIFY_SCOPE}:
            raise GoogleGmailProviderError("gmail_scope_contract_invalid")
        modes = {
            _text(item).lower()
            for item in declaration.get("account_modes") or ("delegated_user",)
        }
        if "delegated_user" not in modes:
            raise GoogleGmailProviderError("gmail_account_mode_not_declared")
        destination = _text(
            declaration.get("destination") or declaration.get("host")
        ).lower()
        if destination and destination != "gmail.googleapis.com":
            raise GoogleGmailProviderError("gmail_destination_contract_invalid")
        return release, declaration

    @staticmethod
    def _declared_account_id(declaration: Mapping[str, Any]) -> str:
        account_id = _text(declaration.get("account_id") or GOOGLE_GMAIL_PROVIDER_ID)
        if not _IDENTIFIER.fullmatch(account_id):
            raise GoogleGmailProviderError("gmail_account_id_invalid")
        return account_id

    def begin_authorization(
        self,
        *,
        application_id: str,
        release_digest: str,
        subject_ref: str,
        account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
        candidate_permission_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_oauth_configuration()
        if not self.client_id or not self.redirect_uri:
            raise GoogleGmailProviderError("google_oauth_not_configured")
        _release, declaration = self._provider_declaration(
            application_id,
            release_digest,
            candidate_permission_profile=candidate_permission_profile,
        )
        declared_account_id = self._declared_account_id(declaration)
        if _text(account_id) != declared_account_id:
            raise GoogleGmailProviderError("gmail_account_not_declared")
        subject = _text(subject_ref)
        if not subject.startswith("user:"):
            raise GoogleGmailProviderError("gmail_delegated_user_required")
        previous = next(
            (
                item
                for item in self.access.connected_accounts(
                    application_id, subject_ref=subject
                )
                if item.get("provider_id") == GOOGLE_GMAIL_PROVIDER_ID
                and item.get("account_id") == declared_account_id
            ),
            None,
        )
        expected_revision = int((previous or {}).get("revision") or 0)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        issued_at = float(self.clock())
        pending = {
            "schema": "adaos.provider.google.gmail.oauth_state.v1",
            "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
            "application_id": application_id,
            "release_digest": release_digest,
            "subject_ref": subject,
            "account_id": declared_account_id,
            "scopes": [GMAIL_MODIFY_SCOPE],
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
            "expected_revision": expected_revision,
            "issued_at": issued_at,
            "expires_at": issued_at + _OAUTH_STATE_TTL_S,
        }
        if candidate_permission_profile is not None:
            pending["candidate_permission_profile"] = dict(
                candidate_permission_profile
            )
        self._vault_put_json(self._state_key(state), pending)
        authorization_url = GOOGLE_AUTHORIZATION_ENDPOINT + "?" + urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": GMAIL_MODIFY_SCOPE,
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent select_account",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return {
            "ok": True,
            "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
            "account_id": declared_account_id,
            "authorization_url": authorization_url,
            "expires_at": _utc_iso(pending["expires_at"]),
        }

    def reusable_connections(
        self,
        *,
        application_id: str,
        release_digest: str,
        subject_ref: str,
        account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
        candidate_permission_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Project provider-owned credentials that this Application may attach.

        Credentials remain in the Core vault.  The projection is intentionally
        redacted and is available only for the resolved user subject and an
        Application release which declares the same provider contract.
        """

        _release, declaration = self._provider_declaration(
            application_id,
            release_digest,
            candidate_permission_profile=candidate_permission_profile,
        )
        declared_account_id = self._declared_account_id(declaration)
        if _text(account_id) != declared_account_id:
            raise GoogleGmailProviderError("gmail_account_not_declared")
        subject = _text(subject_ref)
        if not subject.startswith("user:"):
            raise GoogleGmailProviderError("gmail_delegated_user_required")

        target = next(
            (
                item
                for item in self.access.connected_accounts(
                    application_id, subject_ref=subject
                )
                if item.get("provider_id") == GOOGLE_GMAIL_PROVIDER_ID
                and item.get("account_id") == declared_account_id
            ),
            None,
        )
        shared = [
            item
            for item in self.access.connected_accounts(subject_ref=subject)
            if item.get("application_id") != application_id
            and item.get("provider_id") == GOOGLE_GMAIL_PROVIDER_ID
            and item.get("account_id") == declared_account_id
            and item.get("status") in {"connected", "expired"}
        ]
        credential = self._vault_get_json(
            self._account_key(subject, declared_account_id)
        )
        identity = {
            "schema": "adaos.provider.google.gmail.credential.v1",
            "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
            "subject_ref": subject,
            "account_id": declared_account_id,
        }
        usable = bool(shared) and credential is not None and all(
            credential.get(key) == value for key, value in identity.items()
        )
        usable = usable and GMAIL_MODIFY_SCOPE in set(
            (credential or {}).get("scopes") or ()
        )
        accounts: list[dict[str, Any]] = []
        if usable:
            expires_at = float((credential or {}).get("expires_at") or 0.0)
            accounts.append(
                {
                    "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
                    "account_id": declared_account_id,
                    "email_address": _text(
                        (credential or {}).get("email_address")
                    ),
                    "scopes": [GMAIL_MODIFY_SCOPE],
                    "status": (
                        "connected"
                        if expires_at > float(self.clock())
                        else "expired"
                    ),
                    "attached": bool(
                        target
                        and target.get("status") in {"connected", "expired"}
                    ),
                }
            )
        return {
            "ok": True,
            "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
            "accounts": accounts,
        }

    def attach_reusable_connection(
        self,
        *,
        application_id: str,
        release_digest: str,
        subject_ref: str,
        account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
        candidate_permission_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Attach an existing provider credential to a second Application.

        This mutates only the target Application's redacted access record.  The
        provider credential is neither copied nor returned.
        """

        available = self.reusable_connections(
            application_id=application_id,
            release_digest=release_digest,
            subject_ref=subject_ref,
            account_id=account_id,
            candidate_permission_profile=candidate_permission_profile,
        )
        if not available["accounts"]:
            raise GoogleGmailProviderError("gmail_reusable_account_not_found")
        projected = dict(available["accounts"][0])
        existing = next(
            (
                item
                for item in self.access.connected_accounts(
                    application_id, subject_ref=subject_ref
                )
                if item.get("provider_id") == GOOGLE_GMAIL_PROVIDER_ID
                and item.get("account_id") == account_id
            ),
            None,
        )
        if existing and existing.get("status") in {"connected", "expired"}:
            return {
                "ok": True,
                **projected,
                "attached": True,
                "reused_credential": True,
            }

        credential = self._vault_get_json(
            self._account_key(subject_ref, account_id)
        )
        if credential is None:
            raise GoogleGmailProviderError("gmail_reusable_account_not_found")
        expires_at = float(credential.get("expires_at") or 0.0)
        account = self.access.put_connected_account(
            application_id,
            {
                "release_digest": release_digest,
                "account_id": account_id,
                "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
                "subject_ref": subject_ref,
                "mode": "delegated_user",
                "scopes": [GMAIL_MODIFY_SCOPE],
                "status": (
                    "connected" if expires_at > float(self.clock()) else "expired"
                ),
                "token_expires_at": _utc_iso(expires_at) if expires_at else None,
            },
            expected_revision=int((existing or {}).get("revision") or 0),
            candidate_permission_profile=candidate_permission_profile,
        )
        self.applications.store.append_application_access_audit(
            {
                "occurred_at": _utc_iso(float(self.clock())),
                "action": "connected_account_attached",
                "application_id": application_id,
                "subject_ref": subject_ref,
                "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
                "account_id": account_id,
                "status": account["status"],
                "credential_reused": True,
            }
        )
        return {
            "ok": True,
            **projected,
            "status": account["status"],
            "attached": True,
            "reused_credential": True,
        }

    def _transport_request(self, method: str, url: str, **kwargs: Any) -> Any:
        try:
            return self.transport.request(method, url, **kwargs)
        except Exception as exc:
            raise GoogleGmailProviderError(
                "gmail_provider_unavailable", retryable=True
            ) from exc

    def _mark_denied(self, pending: Mapping[str, Any]) -> None:
        try:
            self.access.put_connected_account(
                str(pending["application_id"]),
                {
                    "release_digest": str(pending["release_digest"]),
                    "account_id": str(pending["account_id"]),
                    "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
                    "subject_ref": str(pending["subject_ref"]),
                    "mode": "delegated_user",
                    "scopes": list(pending["scopes"]),
                    "status": "denied",
                },
                expected_revision=int(pending["expected_revision"]),
            )
        except Exception:
            # A concurrent setup change wins.  OAuth denial never overwrites it.
            return

    def complete_authorization(
        self,
        *,
        state: str,
        code: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        state_value = _text(state)
        if not state_value:
            raise GoogleGmailProviderError("oauth_state_missing")
        state_key = self._state_key(state_value)
        pending = self._vault_get_json(state_key)
        if pending is None:
            raise GoogleGmailProviderError("oauth_state_invalid")
        self._vault_delete(state_key)
        if pending.get("schema") != "adaos.provider.google.gmail.oauth_state.v1":
            raise GoogleGmailProviderError("oauth_state_invalid")
        if float(pending.get("expires_at") or 0.0) <= float(self.clock()):
            raise GoogleGmailProviderError("oauth_state_expired")
        self._provider_declaration(
            str(pending["application_id"]),
            str(pending["release_digest"]),
            candidate_permission_profile=(
                pending.get("candidate_permission_profile")
                if isinstance(pending.get("candidate_permission_profile"), Mapping)
                else None
            ),
        )
        if _text(error):
            self._mark_denied(pending)
            raise GoogleGmailProviderError("oauth_authorization_denied")
        authorization_code = _text(code)
        if not authorization_code:
            raise GoogleGmailProviderError("oauth_code_missing")
        self._ensure_oauth_configuration()
        if not self.client_id:
            raise GoogleGmailProviderError("google_oauth_not_configured")
        token_form = {
            "client_id": self.client_id,
            "code": authorization_code,
            "code_verifier": str(pending["code_verifier"]),
            "grant_type": "authorization_code",
            "redirect_uri": str(pending["redirect_uri"]),
        }
        if self.client_secret:
            token_form["client_secret"] = self.client_secret
        response = self._transport_request(
            "POST",
            GOOGLE_TOKEN_ENDPOINT,
            data=token_form,
            timeout=(5.0, 15.0),
        )
        if int(getattr(response, "status_code", 0) or 0) != 200:
            raise GoogleGmailProviderError("oauth_token_exchange_failed")
        token = _safe_json(response, error_code="oauth_token_response_invalid")
        access_token = _text(token.get("access_token"))
        if not access_token:
            raise GoogleGmailProviderError("oauth_access_token_missing")
        subject_ref = str(pending["subject_ref"])
        account_id = str(pending["account_id"])
        account_key = self._account_key(subject_ref, account_id)
        previous_token = self._vault_get_json(account_key)
        refresh_token = _text(token.get("refresh_token")) or _text(
            (previous_token or {}).get("refresh_token")
        )
        if not refresh_token:
            raise GoogleGmailProviderError("oauth_refresh_token_missing")
        observed_scopes = {
            item for item in _text(token.get("scope")).split() if item
        } or set(pending["scopes"])
        if not set(pending["scopes"]).issubset(observed_scopes):
            raise GoogleGmailProviderError("oauth_required_scope_missing")
        expires_in = max(1, int(token.get("expires_in") or 3600))
        expires_at = float(self.clock()) + expires_in
        profile_response = self._transport_request(
            "GET",
            f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=(5.0, 15.0),
        )
        if int(getattr(profile_response, "status_code", 0) or 0) != 200:
            raise GoogleGmailProviderError("gmail_profile_unavailable")
        profile = _safe_json(
            profile_response, error_code="gmail_profile_response_invalid"
        )
        email_address = _text(profile.get("emailAddress"))
        credential = {
            "schema": "adaos.provider.google.gmail.credential.v1",
            "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
            "subject_ref": subject_ref,
            "account_id": account_id,
            "email_address": email_address,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": _text(token.get("token_type")) or _TOKEN_TYPE,
            "scopes": sorted(observed_scopes),
            "expires_at": expires_at,
            "updated_at": float(self.clock()),
        }
        self._vault_put_json(account_key, credential)
        try:
            account = self.access.put_connected_account(
                str(pending["application_id"]),
                {
                    "release_digest": str(pending["release_digest"]),
                    "account_id": account_id,
                    "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
                    "subject_ref": subject_ref,
                    "mode": "delegated_user",
                    "scopes": list(pending["scopes"]),
                    "status": "connected",
                    "token_expires_at": _utc_iso(expires_at),
                },
                expected_revision=int(pending["expected_revision"]),
                candidate_permission_profile=(
                    pending.get("candidate_permission_profile")
                    if isinstance(
                        pending.get("candidate_permission_profile"), Mapping
                    )
                    else None
                ),
            )
        except Exception as exc:
            if previous_token is None:
                self._vault_delete(account_key)
            else:
                self._vault_put_json(account_key, previous_token)
            raise GoogleGmailProviderError("connected_account_conflict") from exc
        return {
            "ok": True,
            "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
            "account_id": account_id,
            "status": account["status"],
            "email_address": email_address,
            "scopes": list(pending["scopes"]),
        }

    def _refresh(self, credential: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_oauth_configuration()
        if not self.client_id:
            raise GoogleGmailProviderError("google_oauth_not_configured")
        refresh_token = _text(credential.get("refresh_token"))
        if not refresh_token:
            raise GoogleGmailProviderError("gmail_reconnect_required")
        form = {
            "client_id": self.client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        if self.client_secret:
            form["client_secret"] = self.client_secret
        response = self._transport_request(
            "POST", GOOGLE_TOKEN_ENDPOINT, data=form, timeout=(5.0, 15.0)
        )
        if int(getattr(response, "status_code", 0) or 0) != 200:
            raise GoogleGmailProviderError("gmail_reconnect_required")
        token = _safe_json(response, error_code="oauth_token_response_invalid")
        access_token = _text(token.get("access_token"))
        if not access_token:
            raise GoogleGmailProviderError("gmail_reconnect_required")
        updated = dict(credential)
        updated.update(
            {
                "access_token": access_token,
                "token_type": _text(token.get("token_type")) or _TOKEN_TYPE,
                "expires_at": float(self.clock())
                + max(1, int(token.get("expires_in") or 3600)),
                "updated_at": float(self.clock()),
            }
        )
        if _text(token.get("refresh_token")):
            updated["refresh_token"] = _text(token.get("refresh_token"))
        return updated

    def _authorized_credential(
        self,
        *,
        application_id: str,
        release_digest: str,
        subject_ref: str,
        account_id: str,
        candidate_permission_profile: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        _release, declaration = self._provider_declaration(
            application_id,
            release_digest,
            candidate_permission_profile=candidate_permission_profile,
        )
        declared_account_id = self._declared_account_id(declaration)
        if account_id != declared_account_id:
            raise GoogleGmailProviderError("gmail_account_not_declared")
        account = next(
            (
                item
                for item in self.access.connected_accounts(
                    application_id, subject_ref=subject_ref
                )
                if item.get("provider_id") == GOOGLE_GMAIL_PROVIDER_ID
                and item.get("account_id") == account_id
            ),
            None,
        )
        if account is None or account.get("status") not in {"connected", "expired"}:
            raise GoogleGmailProviderError("gmail_account_not_connected")
        account_key = self._account_key(subject_ref, account_id)
        credential = self._vault_get_json(account_key)
        identity = {
            "schema": "adaos.provider.google.gmail.credential.v1",
            "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
            "subject_ref": subject_ref,
            "account_id": account_id,
        }
        if credential is None or any(
            credential.get(key) != value for key, value in identity.items()
        ):
            raise GoogleGmailProviderError("gmail_reconnect_required")
        if GMAIL_MODIFY_SCOPE not in set(credential.get("scopes") or ()):
            raise GoogleGmailProviderError("gmail_required_scope_missing")
        if float(credential.get("expires_at") or 0.0) <= float(self.clock()) + _REFRESH_SKEW_S:
            credential = self._refresh(credential)
            self._vault_put_json(account_key, credential)
            account = self.access.put_connected_account(
                application_id,
                {
                    "release_digest": release_digest,
                    "account_id": account_id,
                    "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
                    "subject_ref": subject_ref,
                    "mode": "delegated_user",
                    "scopes": [GMAIL_MODIFY_SCOPE],
                    "status": "connected",
                    "token_expires_at": _utc_iso(float(credential["expires_at"])),
                },
                expected_revision=int(account["revision"]),
                candidate_permission_profile=candidate_permission_profile,
            )
        return credential, account

    @staticmethod
    def _message_id(value: str) -> str:
        token = _text(value)
        if not _MESSAGE_ID.fullmatch(token):
            raise GoogleGmailProviderError("gmail_message_id_invalid")
        return token

    @staticmethod
    def _label_ids(values: Any) -> list[str]:
        result = sorted({_text(item) for item in values or () if _text(item)})
        if any(not _LABEL_ID.fullmatch(item) for item in result):
            raise GoogleGmailProviderError("gmail_label_id_invalid")
        return result

    def execute(
        self,
        operation: str,
        *,
        application_id: str,
        release_digest: str,
        subject_ref: str,
        account_id: str = GOOGLE_GMAIL_PROVIDER_ID,
        arguments: Mapping[str, Any] | None = None,
        candidate_permission_profile: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        op = _text(operation).lower()
        if op not in _OPERATIONS:
            raise GoogleGmailProviderError("gmail_operation_not_supported")
        args = dict(arguments or {})
        credential, _account = self._authorized_credential(
            application_id=application_id,
            release_digest=release_digest,
            subject_ref=subject_ref,
            account_id=_text(account_id),
            candidate_permission_profile=candidate_permission_profile,
        )
        method = "GET"
        url = ""
        params: dict[str, Any] | None = None
        body: dict[str, Any] | None = None
        if op == "connection_status":
            return {
                "ok": True,
                "provider_id": GOOGLE_GMAIL_PROVIDER_ID,
                "account_id": account_id,
                "status": "connected",
                "email_address": _text(credential.get("email_address")),
                "scopes": [GMAIL_MODIFY_SCOPE],
            }
        if op == "list_messages":
            max_results = int(args.get("max_results") or 50)
            if not 1 <= max_results <= 100:
                raise GoogleGmailProviderError("gmail_page_size_invalid")
            url = f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/messages"
            params = {"maxResults": max_results}
            if _text(args.get("query")):
                params["q"] = _text(args["query"])[:1000]
            labels = self._label_ids(args.get("label_ids"))
            if labels:
                params["labelIds"] = labels
            if _text(args.get("page_token")):
                params["pageToken"] = _text(args["page_token"])[:2048]
        elif op == "get_message":
            message_id = self._message_id(str(args.get("message_id") or ""))
            format_value = _text(args.get("format") or "full").lower()
            if format_value not in {"minimal", "full", "metadata"}:
                raise GoogleGmailProviderError("gmail_message_format_invalid")
            url = f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/messages/{quote(message_id, safe='')}"
            params = {"format": format_value}
        elif op == "list_labels":
            url = f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/labels"
        elif op == "modify_message":
            method = "POST"
            message_id = self._message_id(str(args.get("message_id") or ""))
            add = self._label_ids(args.get("add_label_ids"))
            remove = self._label_ids(args.get("remove_label_ids"))
            if not add and not remove:
                raise GoogleGmailProviderError("gmail_label_change_empty")
            url = f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/messages/{quote(message_id, safe='')}/modify"
            body = {"addLabelIds": add, "removeLabelIds": remove}
        elif op == "trash_message":
            method = "POST"
            message_id = self._message_id(str(args.get("message_id") or ""))
            url = f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/messages/{quote(message_id, safe='')}/trash"
            body = {}
        elif op == "send_message":
            method = "POST"
            raw = _text(args.get("raw"))
            if (
                not raw
                or not _BASE64URL.fullmatch(raw)
                or len(raw.encode("ascii")) > _MAX_SEND_BYTES
            ):
                raise GoogleGmailProviderError("gmail_message_size_invalid")
            if not _text(args.get("idempotency_key")):
                raise GoogleGmailProviderError("gmail_idempotency_key_required")
            url = f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/messages/send"
            body = {"raw": raw}
        headers = {
            "Authorization": f"Bearer {credential['access_token']}",
            "Accept": "application/json",
        }
        if op == "list_message_summaries":
            max_results = int(args.get("max_results") or 20)
            if not 1 <= max_results <= _MAX_BATCH_MESSAGES:
                raise GoogleGmailProviderError("gmail_page_size_invalid")
            list_params: dict[str, Any] = {"maxResults": max_results}
            if _text(args.get("query")):
                list_params["q"] = _text(args["query"])[:1000]
            labels = self._label_ids(args.get("label_ids"))
            if labels:
                list_params["labelIds"] = labels
            if _text(args.get("page_token")):
                list_params["pageToken"] = _text(args["page_token"])[:2048]
            listed = self._authorized_json_request(
                "GET",
                f"{GMAIL_API_ORIGIN}/gmail/v1/users/me/messages",
                headers=headers,
                params=list_params,
            )
            message_ids = [
                self._message_id(str(item.get("id") or ""))
                for item in (listed.get("messages") or [])[:max_results]
                if isinstance(item, Mapping)
            ]

            if message_ids:
                # Gmail's list endpoint returns ids only. Materialize the bounded
                # first-paint projection through Gmail's native HTTP batch
                # endpoint so the provider performs two network round trips
                # instead of a parallel-but-still-expensive N+1 request set.
                boundary = f"adaos_gmail_{secrets.token_hex(12)}"
                batch_response = self._transport_request(
                    "POST",
                    f"{GMAIL_API_ORIGIN}/batch/gmail/v1",
                    headers={
                        **headers,
                        "Accept": "multipart/mixed",
                        "Content-Type": f"multipart/mixed; boundary={boundary}",
                    },
                    data=_gmail_metadata_batch_body(message_ids, boundary),
                    timeout=(5.0, 20.0),
                )
                batch_status = int(
                    getattr(batch_response, "status_code", 0) or 0
                )
                if batch_status == 401:
                    raise GoogleGmailProviderError(
                        "gmail_reconnect_required", status_code=401
                    )
                if batch_status == 403:
                    raise GoogleGmailProviderError(
                        "gmail_permission_denied", status_code=403
                    )
                if batch_status == 429 or batch_status >= 500:
                    raise GoogleGmailProviderError(
                        "gmail_provider_unavailable",
                        status_code=batch_status,
                        retryable=True,
                    )
                if not 200 <= batch_status < 300:
                    raise GoogleGmailProviderError(
                        "gmail_request_failed", status_code=batch_status
                    )
                messages, omitted_message_ids = _gmail_metadata_batch_payloads(
                    batch_response,
                    requested_message_ids=message_ids,
                )
            else:
                messages = []
                omitted_message_ids = []
            payload = {
                "messages": messages,
                "fetch_strategy": "gmail_http_batch",
                "network_round_trips": 2 if message_ids else 1,
            }
            if omitted_message_ids:
                payload["omitted_message_ids"] = omitted_message_ids
            if _text(listed.get("nextPageToken")):
                payload["nextPageToken"] = _text(listed["nextPageToken"])[:2048]
            return GmailOperationResult(op, payload).to_dict()

        payload = self._authorized_json_request(
            method,
            url,
            params=params,
            json=body,
            headers=headers,
        )
        return GmailOperationResult(op, payload).to_dict()

    def _authorized_json_request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._transport_request(
            method,
            url,
            params=params,
            json=json,
            headers=dict(headers),
            timeout=(5.0, 20.0),
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status == 401:
            raise GoogleGmailProviderError("gmail_reconnect_required", status_code=401)
        if status == 403:
            raise GoogleGmailProviderError("gmail_permission_denied", status_code=403)
        if status == 404:
            raise GoogleGmailProviderError("gmail_resource_not_found", status_code=404)
        if status == 429 or status >= 500:
            raise GoogleGmailProviderError(
                "gmail_provider_unavailable", status_code=status, retryable=True
            )
        if not 200 <= status < 300:
            raise GoogleGmailProviderError("gmail_request_failed", status_code=status)
        return _safe_json(response, error_code="gmail_response_invalid")


__all__ = [
    "DEFAULT_CALLBACK_PATH",
    "GMAIL_MODIFY_SCOPE",
    "GOOGLE_GMAIL_PROVIDER_ID",
    "GOOGLE_GMAIL_SKILL_CAPABILITY",
    "GoogleGmailProvider",
    "GoogleGmailProviderError",
]
