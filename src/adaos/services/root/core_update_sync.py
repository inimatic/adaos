from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

import requests

from adaos.services.agent_context import get_ctx
from adaos.services.core_slots import active_slot_manifest, slot_status
from adaos.services.core_update import read_status
from adaos.services.core_update_policy import core_update_reactions_disabled_reason
from adaos.services.hub_root_protocol_store import ack_stream_message, prepare_stream_message
from adaos.services.root.client import RootHttpClient
from adaos.services.runtime_identity import runtime_identity_snapshot, runtime_instance_id, runtime_transition_role

_CORE_UPDATE_STREAM_FLOW_ID = "hub_root.integration.github_core_update"


def _node_core_update_disabled(conf: Any) -> bool:
    return not bool(getattr(conf, "core_update_enabled", True))


def _core_update_stream_id(conf) -> str:
    subnet_id = str(getattr(conf, "subnet_id", "") or "").strip() or "unknown_hub"
    return f"hub-integration:github-core-update:{subnet_id}:{runtime_instance_id()}"


def _core_update_authority_epoch(conf) -> str:
    manifest = active_slot_manifest() or {}
    subnet_id = str(getattr(conf, "subnet_id", "") or "").strip() or "unknown_hub"
    commit = str(manifest.get("git_commit") or "").strip()
    branch = str(manifest.get("target_rev") or manifest.get("git_branch") or "").strip()
    parts = [f"hub:{subnet_id}"]
    parts.append(f"role:{runtime_transition_role()}")
    parts.append(f"instance:{runtime_instance_id()}")
    if commit:
        parts.append(f"commit:{commit[:12]}")
    elif branch:
        parts.append(f"branch:{branch}")
    return "|".join(parts)


def _root_client(conf) -> RootHttpClient | None:
    try:
        ctx = get_ctx()
    except Exception:
        return None
    base_url = str(getattr(getattr(conf, "root_settings", None), "base_url", None) or getattr(ctx.settings, "api_base", None) or "").rstrip("/")
    if not base_url:
        return None
    cert_path = conf.hub_cert_path()
    key_path = conf.hub_key_path()
    ca_path = conf.ca_cert_path()
    if not cert_path.exists() or not key_path.exists():
        return None
    verify: str | bool = str(ca_path) if ca_path.exists() else True
    return RootHttpClient(base_url=base_url, verify=verify, cert=(str(cert_path), str(key_path)))


def _truthy_env(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _append_local_update_candidate(
    candidates: list[tuple[str, str]],
    seen: set[tuple[str, str]],
    base_url: str | None,
    path: str,
) -> None:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return
    try:
        parsed = urlparse(base)
    except Exception:
        return
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return
    normalized = (base, str(path or "").strip() or "/api/admin/update/start")
    if normalized in seen:
        return
    seen.add(normalized)
    candidates.append(normalized)


def _local_update_start_candidates(conf: Any) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    supervisor_enabled = (
        _truthy_env("ADAOS_SUPERVISOR_ENABLED")
        or _truthy_env("ADAOS_AUTOSTART_MANAGED")
        or bool(str(os.getenv("ADAOS_SUPERVISOR_URL") or "").strip())
        or bool(str(os.getenv("ADAOS_SUPERVISOR_PORT") or "").strip())
    )
    supervisor_url = str(os.getenv("ADAOS_SUPERVISOR_URL") or "").strip()
    if supervisor_url:
        _append_local_update_candidate(candidates, seen, supervisor_url, "/api/supervisor/update/start")
    if supervisor_enabled:
        supervisor_host = str(os.getenv("ADAOS_SUPERVISOR_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        supervisor_port = str(os.getenv("ADAOS_SUPERVISOR_PORT") or "8776").strip() or "8776"
        _append_local_update_candidate(
            candidates,
            seen,
            f"http://{supervisor_host}:{supervisor_port}",
            "/api/supervisor/update/start",
        )

    runtime_host = str(os.getenv("ADAOS_RUNTIME_HOST") or "").strip()
    runtime_port = str(os.getenv("ADAOS_RUNTIME_PORT") or "").strip()
    if runtime_port:
        _append_local_update_candidate(
            candidates,
            seen,
            f"http://{runtime_host or '127.0.0.1'}:{runtime_port}",
            "/api/admin/update/start",
        )

    self_base_url = str(os.getenv("ADAOS_SELF_BASE_URL") or "").strip()
    if self_base_url:
        _append_local_update_candidate(candidates, seen, self_base_url, "/api/admin/update/start")

    local_api_url = str(getattr(conf, "local_api_url", "") or "").strip()
    if local_api_url:
        _append_local_update_candidate(candidates, seen, local_api_url, "/api/admin/update/start")

    try:
        ctx = get_ctx()
        host = str(getattr(ctx.settings, "host", None) or "127.0.0.1").strip() or "127.0.0.1"
        port = int(getattr(ctx.settings, "port", None) or 8777)
        _append_local_update_candidate(candidates, seen, f"http://{host}:{port}", "/api/admin/update/start")
    except Exception:
        pass

    return candidates


def build_core_update_report(conf) -> dict[str, Any]:
    identity = runtime_identity_snapshot()
    return {
        "status": read_status(),
        "slot_status": slot_status(),
        "node_id": str(getattr(conf, "node_id", "") or ""),
        "subnet_id": str(getattr(conf, "subnet_id", "") or ""),
        "role": str(getattr(conf, "role", "") or ""),
        "runtime_instance_id": str(identity.get("runtime_instance_id") or ""),
        "transition_role": str(identity.get("transition_role") or "active"),
        "runtime": {
            "runtime_instance_id": str(identity.get("runtime_instance_id") or ""),
            "transition_role": str(identity.get("transition_role") or "active"),
            "started_at": identity.get("started_at"),
            "hostname": str(identity.get("hostname") or ""),
        },
    }


def report_hub_core_update_state(conf) -> dict[str, Any] | None:
    client = _root_client(conf)
    if client is None:
        return None
    payload = build_core_update_report(conf)
    protocol_meta = prepare_stream_message(
        stream_id=_core_update_stream_id(conf),
        flow_id=_CORE_UPDATE_STREAM_FLOW_ID,
        traffic_class="integration",
        delivery_class="must_not_lose",
        message_type="state_report",
        payload=payload,
        ttl_ms=300_000,
        authority_epoch=_core_update_authority_epoch(conf),
        ack_required=True,
    )
    payload["reported_at"] = time.time()
    payload["_protocol"] = dict(protocol_meta)
    result = client.hub_core_update_report(payload=payload)
    try:
        ack_stream_message(
            _core_update_stream_id(conf),
            message_id=str(protocol_meta.get("message_id") or ""),
            cursor=int(protocol_meta.get("cursor") or 0),
            duplicate=bool((result or {}).get("duplicate")),
            result="duplicate" if bool((result or {}).get("duplicate")) else "accepted",
        )
    except Exception:
        logging.getLogger("adaos.hub-io").debug("core update stream ack failed", exc_info=True)
    return result


def reconcile_hub_core_update(conf, *, countdown_sec: float = 60.0) -> dict[str, Any] | None:
    if _node_core_update_disabled(conf):
        return {
            "ok": True,
            "skipped": True,
            "reason": "node_core_update_disabled",
        }
    disabled_reason = core_update_reactions_disabled_reason()
    if disabled_reason:
        return {
            "ok": True,
            "skipped": True,
            "reason": disabled_reason,
        }
    client = _root_client(conf)
    if client is None:
        return None
    try:
        report_hub_core_update_state(conf)
    except Exception:
        logging.getLogger("adaos.hub-io").debug("core update state report failed", exc_info=True)
    manifest = active_slot_manifest() or {}
    branch = str(manifest.get("target_rev") or manifest.get("git_branch") or os.getenv("ADAOS_REV") or os.getenv("ADAOS_INIT_REV") or "").strip()
    current_commit = str(manifest.get("git_commit") or "").strip()
    release = client.hub_core_update_release(branch=branch or None, current_commit=current_commit or None)
    if not isinstance(release, dict) or not release.get("ok"):
        return release if isinstance(release, dict) else None
    if not bool(release.get("needs_update")):
        return release
    release_info = release.get("release") if isinstance(release.get("release"), dict) else {}
    target_rev = str(release_info.get("branch") or branch or "").strip()
    head_sha = str(release_info.get("head_sha") or "").strip()
    local_token = str(getattr(conf, "token", "") or os.getenv("ADAOS_TOKEN") or "").strip()
    if not local_token:
        raise RuntimeError("missing local ADAOS token for self-update reconcile")
    body = {
        "target_rev": target_rev,
        "target_version": head_sha if head_sha else "",
        "reason": f"root.release:{target_rev}{(':' + head_sha[:12]) if head_sha else ''}",
        "countdown_sec": float(release_info.get("countdown_sec") or countdown_sec),
        "drain_timeout_sec": 10,
        "signal_delay_sec": 0.25,
    }
    dispatch_errors: list[dict[str, str]] = []
    payload: dict[str, Any] | None = None
    dispatch_url = ""
    for base_url, path in _local_update_start_candidates(conf):
        url = f"{base_url}{path}"
        try:
            response = requests.post(
                url,
                json=body,
                headers={"X-AdaOS-Token": local_token},
                timeout=15,
            )
            response.raise_for_status()
            raw_payload = response.json()
            payload = raw_payload if isinstance(raw_payload, dict) else {"ok": True, "response": raw_payload}
            dispatch_url = url
            break
        except Exception as exc:
            dispatch_errors.append(
                {
                    "url": url,
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            )
    if payload is None:
        raise RuntimeError(f"local update start dispatch failed: {dispatch_errors}")
    return {
        **release,
        "dispatch": payload,
        "dispatch_url": dispatch_url,
    }


__all__ = [
    "build_core_update_report",
    "reconcile_hub_core_update",
    "report_hub_core_update_state",
]
