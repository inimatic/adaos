"""Bounded, read-only Google Drive adapter using Core-owned delegated OAuth.

Credentials never cross the SDK boundary. File IDs are opaque identifiers;
applications cannot supply destinations, HTTP verbs, queries or OAuth scopes.
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

from adaos.services.providers.google_gmail import (
    GoogleGmailProvider, GoogleGmailProviderError, _safe_json, _text,
)

GOOGLE_DRIVE_PROVIDER_ID = "google.drive"
GOOGLE_DRIVE_SKILL_CAPABILITY = "providers.google.drive"
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_API_ORIGIN = "https://www.googleapis.com"
FOLDER_MIME = "application/vnd.google-apps.folder"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
_FIELDS = "id,name,mimeType,size,modifiedTime,parents,thumbnailLink,imageMediaMetadata(width,height),capabilities(canDownload)"


class GoogleDriveProvider(GoogleGmailProvider):
    """Reuse Core OAuth admission; expose only the Drive read operations below."""

    provider_id = GOOGLE_DRIVE_PROVIDER_ID
    oauth_scope = DRIVE_READONLY_SCOPE
    api_host = "www.googleapis.com"
    credential_schema = "adaos.provider.google.drive.credential.v1"
    binding_instance_ref = "binding-instance:google-drive-provider"

    def _fetch_profile_email(self, access_token: str) -> str:
        response = self._transport_request("GET", DRIVE_API_ORIGIN + "/drive/v3/about",
            params={"fields": "user(emailAddress)"}, headers={"Authorization": "Bearer " + access_token},
            timeout=(5.0, 15.0), allow_redirects=False)
        self._check(response)
        return _text(_safe_json(response, error_code="drive_profile_invalid").get("user", {}).get("emailAddress"))

    @staticmethod
    def _id(value: Any) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise GoogleGmailProviderError("drive_file_id_invalid")
        return value

    @staticmethod
    def _check(response: Any) -> None:
        status = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status < 300:
            code = {401: "drive_reconnect_required", 403: "drive_permission_denied",
                    404: "drive_file_not_found", 429: "drive_rate_limited"}.get(status, "drive_request_failed")
            raise GoogleGmailProviderError(code, status_code=status, retryable=status == 429 or status >= 500)

    def _json(self, path: str, token: str, params: Mapping[str, Any]) -> dict[str, Any]:
        response = self._transport_request("GET", DRIVE_API_ORIGIN + "/drive/v3/" + path,
            params=dict(params), headers={"Authorization": "Bearer " + token},
            timeout=(5.0, 20.0), allow_redirects=False)
        self._check(response)
        return _safe_json(response, error_code="drive_response_invalid")

    @staticmethod
    def _file(raw: Mapping[str, Any]) -> dict[str, Any]:
        # Do not return temporary signed URLs; thumbnails pass through Core.
        item = {k: raw[k] for k in ("id", "name", "mimeType", "size", "modifiedTime", "parents", "imageMediaMetadata", "capabilities") if k in raw}
        item["is_folder"] = raw.get("mimeType") == FOLDER_MIME
        item["has_thumbnail"] = bool(raw.get("thumbnailLink"))
        return item

    def _bytes(self, url: str, token: str, *, maximum: int, params=None) -> tuple[bytes, str]:
        response = self._transport_request("GET", url, params=params,
            headers={"Authorization": "Bearer " + token}, timeout=(5.0, 60.0),
            stream=True, allow_redirects=False)
        try:
            self._check(response)
            headers = getattr(response, "headers", {})
            if int(headers.get("Content-Length", "0") or "0") > maximum:
                raise GoogleGmailProviderError("drive_file_too_large")
            chunks, size = [], 0
            for chunk in response.iter_content(256 * 1024):
                size += len(chunk)
                if size > maximum: raise GoogleGmailProviderError("drive_file_too_large")
                chunks.append(chunk)
            return b"".join(chunks), str(headers.get("Content-Type", "application/octet-stream")).split(";", 1)[0]
        finally:
            response.close()

    def execute(self, operation: str, *, application_id: str, release_digest: str,
                subject_ref: str, account_id: str = GOOGLE_DRIVE_PROVIDER_ID,
                arguments: Mapping[str, Any] | None = None,
                candidate_permission_profile: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if operation not in {"connection_status", "list_files", "get_file", "read_file", "read_thumbnail"}:
            raise GoogleGmailProviderError("drive_operation_not_supported")
        args = dict(arguments or {})
        allowed = {"connection_status": set(), "list_files": {"parent_id", "page_token", "page_size"},
                   "get_file": {"file_id"}, "read_file": {"file_id"}, "read_thumbnail": {"file_id"}}
        if set(args) - allowed[operation]: raise GoogleGmailProviderError("drive_arguments_invalid")
        credential, _ = self._authorized_credential(application_id=application_id, release_digest=release_digest,
            subject_ref=subject_ref, account_id=account_id, candidate_permission_profile=candidate_permission_profile)
        result = {"ok": True, "provider_id": self.provider_id, "account_id": account_id}
        token = str(credential["access_token"])
        if operation == "connection_status":
            return {**result, "status": "connected", "email_address": credential.get("email_address", ""), "scopes": [self.oauth_scope]}
        if operation == "list_files":
            parent = self._id(args.get("parent_id", "root"))
            page_size = args.get("page_size", 50)
            page_token = args.get("page_token", "")
            if type(page_size) is not int or not 1 <= page_size <= 100 or not isinstance(page_token, str) or len(page_token) > 4096:
                raise GoogleGmailProviderError("drive_page_invalid")
            params = {"q": f"'{parent}' in parents and trashed = false and (mimeType = '{FOLDER_MIME}' or mimeType contains 'image/')",
                "pageSize": page_size, "orderBy": "folder,name", "spaces": "drive",
                "fields": "nextPageToken,incompleteSearch,files(" + _FIELDS + ")"}
            if page_token: params["pageToken"] = page_token
            payload = self._json("files", token, params)
            return {**result, "items": [self._file(f) for f in payload.get("files", [])],
                    "next_page_token": payload.get("nextPageToken", ""), "incomplete_search": bool(payload.get("incompleteSearch"))}
        identity = self._id(args.get("file_id"))
        metadata = self._json("files/" + quote(identity, safe=""), token, {"fields": _FIELDS, "supportsAllDrives": "true"})
        if operation == "get_file": return {**result, "file": self._file(metadata)}
        if not str(metadata.get("mimeType", "")).startswith("image/"):
            raise GoogleGmailProviderError("drive_image_required")
        if operation == "read_thumbnail":
            thumbnail = str(metadata.get("thumbnailLink", ""))
            parsed = urlsplit(thumbnail)
            host = str(parsed.hostname or "").lower()
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or not host.endswith(".googleusercontent.com"):
                raise GoogleGmailProviderError("drive_thumbnail_unavailable")
            raw, mime = self._bytes(thumbnail, token, maximum=2 * 1024 * 1024)
        else:
            if metadata.get("capabilities", {}).get("canDownload") is False:
                raise GoogleGmailProviderError("drive_download_denied")
            if int(metadata.get("size", 0)) > 64 * 1024 * 1024: raise GoogleGmailProviderError("drive_file_too_large")
            raw, mime = self._bytes(DRIVE_API_ORIGIN + "/drive/v3/files/" + quote(identity, safe=""), token,
                maximum=64 * 1024 * 1024, params={"alt": "media", "supportsAllDrives": "true"})
        if not mime.startswith("image/"): raise GoogleGmailProviderError("drive_image_response_required")
        return {**result, "file": self._file(metadata), "data": raw, "media_type": mime}


__all__ = ["GoogleDriveProvider", "GOOGLE_DRIVE_PROVIDER_ID", "GOOGLE_DRIVE_SKILL_CAPABILITY", "DRIVE_READONLY_SCOPE"]
