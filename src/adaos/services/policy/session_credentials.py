"""Purpose-bound bearer credentials for existing local sessions, not owner tokens."""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import time
from typing import Any

from adaos.domain.personalization_access import ScopeRef, SubjectRef
from adaos.services.personalization_access import PersonalizationAccessService
from .caller import CallerAccessDenied


_PREFIX = "adaos-session-v1."
_PURPOSE = "local_skill_http.v1"


def _active_expiry(value: Any, now: float) -> float:
    try:
        expiry = float(value)
    except (ValueError, TypeError) as exc:
        raise CallerAccessDenied("session_expiry_invalid") from exc
    if not math.isfinite(expiry) or expiry <= now:
        raise CallerAccessDenied("session_expired")
    return expiry


def _session(access: PersonalizationAccessService, session_id: str, now: float) -> dict[str, Any]:
    record = access.store.get_session(session_id)
    if not record or record.get("status") != "active" or record.get("revoked_at") is not None:
        raise CallerAccessDenied("session_inactive")
    if record.get("expires_at") is not None:
        _active_expiry(record["expires_at"], now)
    if not record.get("subject"):
        raise CallerAccessDenied("session_subject_missing")
    if record.get("device_id"):
        device = access.store.get_device_key(record["device_id"])
        if not device or device.get("status") != "active" or device.get("revoked_at") is not None:
            raise CallerAccessDenied("session_device_inactive")
        if record["subject"].get("kind") == "user" and device.get("user_id") != record["subject"].get("id"):
            raise CallerAccessDenied("session_device_subject_mismatch")
    return record


def issue_tool_credential(access: PersonalizationAccessService, *, session_id: str, skill_name: str,
                          actor: SubjectRef, ttl_seconds: int = 3600) -> dict[str, Any]:
    """Owner-only transport provisioning; creates no subject, session or grant."""
    if actor != access.owner:
        raise CallerAccessDenied("session_credential_requires_owner")
    if not 1 <= ttl_seconds <= 3600:
        raise ValueError("credential lifetime must be between 1 and 3600 seconds")
    scope = ScopeRef("skill", skill_name)
    with access.batch():
        now = time.time()
        session = _session(access, session_id, now)
        expires = min(now + ttl_seconds, float(session.get("expires_at") or now + ttl_seconds))
        token = f"{_PREFIX}{session_id}.{secrets.token_urlsafe(32)}"
        credential = {"purpose": _PURPOSE, "scope": scope.to_dict(), "expires_at": expires,
                      "issued_at": now, "issued_by": actor.to_dict(),
                      "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest()}
        access.store.update_session(session_id, {"tool_credential": credential})
        access._audit("session.tool_credential_issued", actor=actor,
                      session=SubjectRef("session", session_id), scope=scope,
                      metadata={"expires_at": expires})
    return {"token_type": "Bearer", "access_token": token, "expires_at": expires,
            "session_id": session_id, "scope": scope.to_dict()}


def authenticate_tool_credential(access: PersonalizationAccessService, token: str) -> tuple[SubjectRef, ScopeRef]:
    if not token.startswith(_PREFIX) or len(token) > 1024:
        raise CallerAccessDenied("session_credential_invalid")
    session_id, separator, secret = token.removeprefix(_PREFIX).rpartition(".")
    if not session_id or not separator or len(secret) != 43:
        raise CallerAccessDenied("session_credential_invalid")
    with access.batch():
        now = time.time()
        session = _session(access, session_id, now)
        credential = session.get("tool_credential") or {}
        if credential.get("purpose") != _PURPOSE:
            raise CallerAccessDenied("session_credential_wrong_purpose")
        _active_expiry(credential.get("expires_at"), now)
        expected = str(credential.get("token_hash") or "")
        if not hmac.compare_digest(expected, hashlib.sha256(token.encode("utf-8")).hexdigest()):
            raise CallerAccessDenied("session_credential_invalid")
        scope = credential.get("scope") or {}
        if scope.get("kind") != "skill" or not scope.get("id"):
            raise CallerAccessDenied("session_credential_scope_invalid")
        return SubjectRef("session", session_id), ScopeRef("skill", scope["id"])
