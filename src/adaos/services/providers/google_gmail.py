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
    ) -> None:
        self.vault = vault
        self.applications = applications
        self.access = ApplicationAccessManagementService(applications)
        self.transport = transport
        self.client_id = _text(client_id)
        self.client_secret = _text(client_secret)
        self.redirect_uri = _text(redirect_uri)
        self.clock = clock

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
        client_id = _text(os.getenv("ADAOS_GOOGLE_OAUTH_CLIENT_ID"))
        client_secret = _text(os.getenv("ADAOS_GOOGLE_OAUTH_CLIENT_SECRET"))
        if not client_id:
            client_id = _text(vault.get("provider:google.oauth:client_id", default=None))
        if not client_secret:
            client_secret = _text(vault.get("provider:google.oauth:client_secret", default=None))
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
        )

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
    ) -> tuple[Any, dict[str, Any]]:
        try:
            release = self.applications.store.get_release(application_id, release_digest)
        except Exception as exc:
            raise GoogleGmailProviderError("application_release_unavailable") from exc
        declarations = {
            _text(item.get("id")).lower(): dict(item)
            for item in release.permission_profile.external_providers
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
    ) -> dict[str, Any]:
        if not self.client_id or not self.redirect_uri:
            raise GoogleGmailProviderError("google_oauth_not_configured")
        _release, declaration = self._provider_declaration(
            application_id, release_digest
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
            str(pending["application_id"]), str(pending["release_digest"])
        )
        if _text(error):
            self._mark_denied(pending)
            raise GoogleGmailProviderError("oauth_authorization_denied")
        authorization_code = _text(code)
        if not authorization_code:
            raise GoogleGmailProviderError("oauth_code_missing")
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
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        _release, declaration = self._provider_declaration(
            application_id, release_digest
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
        response = self._transport_request(
            method,
            url,
            params=params,
            json=body,
            headers=headers,
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
        payload = _safe_json(response, error_code="gmail_response_invalid")
        return GmailOperationResult(op, payload).to_dict()


__all__ = [
    "DEFAULT_CALLBACK_PATH",
    "GMAIL_MODIFY_SCOPE",
    "GOOGLE_GMAIL_PROVIDER_ID",
    "GOOGLE_GMAIL_SKILL_CAPABILITY",
    "GoogleGmailProvider",
    "GoogleGmailProviderError",
]
