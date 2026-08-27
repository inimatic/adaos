"""Durable, resource-scoped approvals for repeated runtime actions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "adaos.runtime_action_grants.v1"
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")
_LOCK = threading.RLock()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _store_path(ctx: Any) -> Path:
    override = _text(os.getenv("ADAOS_RUNTIME_ACTION_GRANTS_PATH"))
    if override:
        return Path(override).expanduser().resolve()
    paths = getattr(ctx, "paths", None)
    state_dir = Path(paths.state_dir()).expanduser().resolve()
    return state_dir / "policy" / "runtime_action_grants.json"


def _empty() -> dict[str, Any]:
    return {"schema": SCHEMA, "revision": 0, "grants": {}}


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return _empty()
    if not isinstance(value.get("grants"), dict):
        value["grants"] = {}
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)


def _grant_id(*, subject: str, scope: str, resource: str, webspace_id: str) -> str:
    raw = "\0".join((subject, scope, resource, webspace_id))
    return "grant.runtime." + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _validated_ref(
    *, subject: str, scope: str, resource: str, webspace_id: str
) -> dict[str, str]:
    values = {
        "subject": _text(subject),
        "scope": _text(scope),
        "resource": _text(resource),
        "webspace_id": _text(webspace_id) or "default",
    }
    if not all(_TOKEN.fullmatch(value) for value in values.values()):
        raise ValueError("invalid_runtime_action_grant_ref")
    return values


def find_runtime_action_grant(
    ctx: Any,
    *,
    subject: str,
    scope: str,
    resource: str,
    webspace_id: str,
    now: float | None = None,
) -> dict[str, Any] | None:
    ref = _validated_ref(
        subject=subject, scope=scope, resource=resource, webspace_id=webspace_id
    )
    grant_id = _grant_id(**ref)
    current = time.time() if now is None else float(now)
    with _LOCK:
        store = _load(_store_path(ctx))
        grant = store["grants"].get(grant_id)
        if not isinstance(grant, dict):
            return None
        if grant.get("status") != "active" or float(grant.get("expires_at") or 0) <= current:
            return None
        return dict(grant)


def remember_runtime_action_grant(
    ctx: Any,
    *,
    subject: str,
    scope: str,
    resource: str,
    webspace_id: str,
    approval_id: str,
    approved_by: str,
    ttl_seconds: int = 30 * 24 * 60 * 60,
    now: float | None = None,
) -> dict[str, Any]:
    ref = _validated_ref(
        subject=subject, scope=scope, resource=resource, webspace_id=webspace_id
    )
    created = time.time() if now is None else float(now)
    ttl = max(300, min(365 * 24 * 60 * 60, int(ttl_seconds or 0)))
    grant_id = _grant_id(**ref)
    grant = {
        "schema": "adaos.runtime_action_grant.v1",
        "id": grant_id,
        **ref,
        "status": "active",
        "approval_id": _text(approval_id),
        "approved_by": _text(approved_by),
        "created_at": created,
        "updated_at": created,
        "expires_at": created + ttl,
    }
    path = _store_path(ctx)
    with _LOCK:
        store = _load(path)
        previous = store["grants"].get(grant_id)
        if isinstance(previous, dict):
            grant["created_at"] = float(previous.get("created_at") or created)
        store["grants"][grant_id] = grant
        store["revision"] = int(store.get("revision") or 0) + 1
        _write(path, store)
    return dict(grant)


def revoke_runtime_action_grant(ctx: Any, grant_id: str) -> bool:
    token = _text(grant_id)
    path = _store_path(ctx)
    with _LOCK:
        store = _load(path)
        grant = store["grants"].get(token)
        if not isinstance(grant, dict) or grant.get("status") == "revoked":
            return False
        grant["status"] = "revoked"
        grant["updated_at"] = time.time()
        store["revision"] = int(store.get("revision") or 0) + 1
        _write(path, store)
    return True


__all__ = [
    "find_runtime_action_grant",
    "remember_runtime_action_grant",
    "revoke_runtime_action_grant",
]
