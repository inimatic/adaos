from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from typing import Any


class SidecarYjsAuthorityError(RuntimeError):
    """Raised when Python attempts to mutate a sidecar-fenced webspace."""


def _eligible_webspaces() -> set[str]:
    raw = str(os.getenv("ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES") or "")
    return {
        value.strip()
        for value in raw.replace(";", ",").split(",")
        if value.strip()
    }


def _status_url(webspace_id: str) -> str:
    raw = str(
        os.getenv("ADAOS_YJS_SIDECAR_AUTHORITY_CONTROL_URL")
        or "http://127.0.0.1:7426/v1/yjs-authority/status"
    ).strip()
    parsed = urllib.parse.urlparse(raw)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.path != "/v1/yjs-authority/status"
        or parsed.username
        or parsed.password
    ):
        raise SidecarYjsAuthorityError("invalid_sidecar_authority_control_url")
    query = urllib.parse.urlencode({"webspace": webspace_id})
    return urllib.parse.urlunparse(parsed._replace(query=query, fragment=""))


def _read_authority_record(webspace_id: str) -> dict[str, Any]:
    token = str(os.getenv("ADAOS_TOKEN") or "").strip()
    if not token:
        raise SidecarYjsAuthorityError("sidecar_authority_token_missing")
    request = urllib.request.Request(
        _status_url(webspace_id),
        method="GET",
        headers={"X-AdaOS-Token": token},
    )
    timeout_s = max(
        0.1,
        float(os.getenv("ADAOS_YJS_SIDECAR_AUTHORITY_TIMEOUT_S") or "0.75"),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
            if int(response.status) != 200:
                raise SidecarYjsAuthorityError(
                    f"sidecar_authority_status_{response.status}"
                )
            payload = json.loads(response.read(1 << 20) or b"{}")
    except SidecarYjsAuthorityError:
        raise
    except Exception as exc:
        raise SidecarYjsAuthorityError(
            f"sidecar_authority_unavailable:{type(exc).__name__}"
        ) from exc
    record = payload.get("record") if isinstance(payload, dict) else None
    if not isinstance(record, dict):
        raise SidecarYjsAuthorityError("sidecar_authority_record_missing")
    return record


async def assert_python_yjs_write_authority(webspace_id: str) -> None:
    target = str(webspace_id or "").strip() or "default"
    eligible = _eligible_webspaces()
    if target not in eligible:
        return
    record = await asyncio.to_thread(_read_authority_record, target)
    owner = str(record.get("owner") or "").strip().lower()
    state = str(record.get("state") or "").strip().lower()
    if owner != "python" or state != "stable":
        generation = int(record.get("generation") or 0)
        raise SidecarYjsAuthorityError(
            f"python_yjs_write_fenced:{target}:{owner or 'unknown'}:"
            f"{state or 'unknown'}:generation={generation}"
        )
