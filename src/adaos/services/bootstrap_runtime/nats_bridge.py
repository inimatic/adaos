from __future__ import annotations

import json as _json
import os
import time
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse

from adaos.services.bounded_io import bounded_text_tail_lines
from adaos.services.nats_config import (
    PUBLIC_NATS_WS_DEDICATED,
    normalize_nats_ws_url,
    nats_url_uses_websocket,
    public_nats_tcp_candidates,
    public_nats_ws_api,
    public_nats_ws_candidates,
)
from adaos.services.realtime_sidecar import realtime_sidecar_diag_path, realtime_sidecar_local_url
from adaos.services.runtime_identity import runtime_transition_role

from .status_policy import _env_truthy


def _read_sidecar_tail_lines(path: Path, *, lines: int) -> list[str]:
    try:
        max_read_bytes = int(os.getenv("HUB_SIDECAR_TAIL_READ_BYTES", "262144") or "262144")
    except Exception:
        max_read_bytes = 262144
    try:
        max_line_chars = int(os.getenv("HUB_SIDECAR_TAIL_MAX_LINE_CHARS", "4096") or "4096")
    except Exception:
        max_line_chars = 4096
    return bounded_text_tail_lines(
        path,
        limit=max(0, int(lines or 0)),
        max_bytes=max_read_bytes,
        max_line_chars=max_line_chars,
    )


def _nats_credentials_refresh_evidence(
    err: Exception,
    *,
    server: str | None,
    sidecar_diag_file: Path | None = None,
) -> str | None:
    try:
        message = str(err or "").strip().lower()
    except Exception:
        message = ""
    explicit_auth_markers = (
        "authentication failure",
        "authentication timeout",
        "authorization violation",
        "invalid credentials",
        "invalid user credentials",
    )
    if any(marker in message for marker in explicit_auth_markers):
        return "explicit_auth_error"
    if isinstance(err, TypeError) and "argument of type 'int' is not iterable" in message:
        return "explicit_auth_error"

    is_eof = type(err).__name__ == "UnexpectedEOF" or "unexpected eof" in message
    if not is_eof or str(server or "").strip() != realtime_sidecar_local_url():
        return None

    diag_path = Path(sidecar_diag_file) if sidecar_diag_file is not None else realtime_sidecar_diag_path()
    try:
        max_age_s = float(os.getenv("HUB_NATS_AUTH_DIAG_MAX_AGE_S", "60") or "60")
    except Exception:
        max_age_s = 60.0
    max_age_s = max(1.0, min(max_age_s, 300.0))
    now = time.time()
    for line in reversed(_read_sidecar_tail_lines(diag_path, lines=8)):
        try:
            record = _json.loads(line)
            recorded_at = float(record.get("ts"))
            last_error = str(record.get("last_error") or "").strip().lower()
        except (AttributeError, TypeError, ValueError, _json.JSONDecodeError):
            continue
        age_s = now - recorded_at
        if age_s < -5.0 or age_s > max_age_s:
            continue
        if any(marker in last_error for marker in explicit_auth_markers):
            return "sidecar_auth_failure_after_eof"
    return None


def _should_refresh_nats_credentials(
    err: Exception,
    *,
    server: str | None,
    sidecar_diag_file: Path | None = None,
) -> bool:
    return (
        _nats_credentials_refresh_evidence(
            err,
            server=server,
            sidecar_diag_file=sidecar_diag_file,
        )
        is not None
    )


def _hub_root_transport_kind(server: str | None) -> str | None:
    text = str(server or "").strip().lower()
    if not text:
        return None
    if text.startswith(("ws://", "wss://")):
        return "ws"
    if text.startswith(("nats://", "tls://")):
        return "tcp"
    if text.startswith(("http://", "https://")):
        return "sidecar"
    return None


def _hub_nats_prefer_dedicated() -> str:
    raw = os.getenv("HUB_NATS_PREFER_DEDICATED")
    text = str(raw or "").strip()
    if text:
        return text
    return "0"


def _normalize_hub_nats_ws_url(value: str | None) -> str | None:
    normalized = normalize_nats_ws_url(value, fallback=None)
    if _hub_nats_prefer_dedicated() == "1":
        return normalized
    if normalized == PUBLIC_NATS_WS_DEDICATED:
        return public_nats_ws_api()
    return normalized


def _hub_public_ws_candidates(base_url: str | None) -> list[str]:
    prefer_dedicated = _hub_nats_prefer_dedicated()
    normalized_base = _normalize_hub_nats_ws_url(base_url)

    candidates: list[str] = []
    if normalized_base and nats_url_uses_websocket(normalized_base):
        candidates.append(normalized_base)
    for item in public_nats_ws_candidates(
        prefer_dedicated=prefer_dedicated,
        allow_dedicated_fallback=prefer_dedicated == "1",
    ):
        if item not in candidates:
            candidates.append(item)
    return candidates


def _hub_public_tcp_candidates(base_url: str | None) -> list[str]:
    prefer_dedicated = _hub_nats_prefer_dedicated()
    candidates: list[str] = []
    base = str(base_url or "").strip()
    if base:
        candidates.append(base)
    for item in public_nats_tcp_candidates(
        prefer_dedicated=prefer_dedicated,
        allow_dedicated_fallback=prefer_dedicated == "1",
    ):
        if item not in candidates:
            candidates.append(item)
    return candidates


def _runtime_candidate_mode() -> bool:
    return runtime_transition_role() == "candidate"


def _hub_root_candidate_passive_mode() -> bool:
    return _runtime_candidate_mode()


def _nats_url_needs_public_ws_refresh(value: str | None) -> bool:
    raw = str(value or "").strip()
    if not raw or nats_url_uses_websocket(raw):
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if (parsed.scheme or "").lower() != "nats":
        return False
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return False
    return host == "api.inimatic.com" or host.endswith(".inimatic.com")


def _build_realtime_sidecar_fallback_candidates(
    candidates: Sequence[str | None],
    *,
    local_candidate: str,
) -> list[str]:
    allow_tcp_fallback = _env_truthy(os.getenv("ADAOS_REALTIME_ALLOW_TCP_FALLBACK"), default=False)
    fallback_candidates: list[str] = []
    for item in candidates:
        try:
            candidate_text = str(item or "").strip()
        except Exception:
            continue
        if not candidate_text or candidate_text == local_candidate:
            continue
        if candidate_text.startswith("ws"):
            if candidate_text not in fallback_candidates:
                fallback_candidates.append(candidate_text)
            continue
        if not allow_tcp_fallback:
            continue
        if candidate_text not in fallback_candidates:
            fallback_candidates.append(candidate_text)
    return fallback_candidates


def _should_quarantine_nats_candidate(candidate: str | None, *, local_sidecar_url: str | None = None) -> bool:
    candidate_text = str(candidate or "").strip()
    if not candidate_text:
        return False
    sidecar_text = str(local_sidecar_url or "").strip()
    if sidecar_text and candidate_text == sidecar_text:
        return False
    return True


def _hub_nats_sidecar_failover_on_transient() -> bool:
    # A healthy sidecar listener owns the hub-root transport boundary.  Moving
    # an established runtime back and forth between the local byte relay and a
    # direct WSS client creates two competing reconnect loops and repeatedly
    # rebuilds all NATS subscriptions.  Direct fallback remains available when
    # the sidecar listener itself is unavailable; quarantining a live listener
    # after a remote EOF is an explicit emergency opt-in only.
    return _env_truthy(os.getenv("HUB_NATS_SIDECAR_FAILOVER_ON_TRANSIENT"), default=False)


def _hub_nats_sidecar_quarantine_s() -> float:
    try:
        value = float(os.getenv("HUB_NATS_SIDECAR_QUARANTINE_S", "300") or "300")
    except Exception:
        value = 300.0
    if value < 5.0:
        return 5.0
    if value > 3600.0:
        return 3600.0
    return value


def _resolve_nats_log_server(
    *,
    server: str | None = None,
    current_attempt: str | None = None,
    connected_server: str | None = None,
) -> str | None:
    for value in (server, current_attempt, connected_server):
        text = str(value or "").strip()
        if text:
            return text
    return None


def _hub_id_from_nats_user(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("hub_") and len(raw) > 4:
        return raw[4:]
    return None


def _canonical_hub_nats_identity(
    *,
    local_hub_id: str | None,
    nats_user: str | None,
    response_hub_id: str | None = None,
) -> tuple[str | None, str | None]:
    resolved_hub_id = (
        str(response_hub_id or "").strip()
        or _hub_id_from_nats_user(nats_user)
        or str(local_hub_id or "").strip()
        or None
    )
    if resolved_hub_id:
        return resolved_hub_id, f"hub_{resolved_hub_id}"
    resolved_user = str(nats_user or "").strip() or None
    return None, resolved_user


class NatsBridgePolicy:
    """Typed owner for NATS candidate, identity, and recovery policy.

    The module functions remain as compatibility exports for focused callers;
    bootstrap composition depends on this object instead of importing an
    unstructured set of helpers into its operational scope.
    """

    read_sidecar_tail_lines = staticmethod(_read_sidecar_tail_lines)
    credentials_refresh_evidence = staticmethod(_nats_credentials_refresh_evidence)
    should_refresh_credentials = staticmethod(_should_refresh_nats_credentials)
    transport_kind = staticmethod(_hub_root_transport_kind)
    prefer_dedicated = staticmethod(_hub_nats_prefer_dedicated)
    normalize_ws_url = staticmethod(_normalize_hub_nats_ws_url)
    public_ws_candidates = staticmethod(_hub_public_ws_candidates)
    public_tcp_candidates = staticmethod(_hub_public_tcp_candidates)
    runtime_candidate_mode = staticmethod(_runtime_candidate_mode)
    candidate_passive_mode = staticmethod(_hub_root_candidate_passive_mode)
    url_needs_public_ws_refresh = staticmethod(_nats_url_needs_public_ws_refresh)
    sidecar_fallback_candidates = staticmethod(_build_realtime_sidecar_fallback_candidates)
    should_quarantine_candidate = staticmethod(_should_quarantine_nats_candidate)
    sidecar_failover_on_transient = staticmethod(_hub_nats_sidecar_failover_on_transient)
    sidecar_quarantine_s = staticmethod(_hub_nats_sidecar_quarantine_s)
    resolve_log_server = staticmethod(_resolve_nats_log_server)
    hub_id_from_user = staticmethod(_hub_id_from_nats_user)
    canonical_identity = staticmethod(_canonical_hub_nats_identity)
