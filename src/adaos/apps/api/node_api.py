from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import gc
import mimetypes
import os
import re
import sys
import time
import threading
import tracemalloc
import uuid
from collections import Counter
from functools import partial
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

import anyio
import requests
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from adaos.domain import Event, client_subscription_contract_snapshot, event_envelope_contract_snapshot
from adaos.adapters.db import SqliteSkillRegistry
from adaos.apps.api.auth import ensure_token, require_token, resolve_presented_token
from adaos.services.agent_context import get_ctx
from adaos.services.bootstrap import (
    is_ready,
    load_config,
    request_hub_root_reconnect,
    request_member_hub_refresh,
    request_member_hub_reconnect,
    request_hub_root_route_reset,
    switch_role,
)
from adaos.services.node_display import node_display_from_config
from adaos.services.env_policy import env_bool
from adaos.services.io_web.desktop import WebDesktopInstalled, WebDesktopService
from adaos.services.media_library import (
    ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES,
    ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES,
    guess_media_type,
    list_media_files,
    media_capabilities,
    media_file_path,
    media_snapshot,
)
from adaos.services.media_core import (
    MediaResource,
    file_range_iter,
    media_content_response_parts,
    media_resource_from_path,
    parse_media_range,
    resolve_media_reference,
)
from adaos.services.media_delivery_activity import (
    begin_media_delivery,
    end_media_delivery,
    touch_media_delivery,
)
from adaos.services.media_indexer_library import (
    resolve_media_indexer_resource,
    resolve_media_indexer_resource_by_name,
)
from adaos.services.node_config import set_node_names as save_node_names_config
from adaos.services.reliability import (
    _state_sync_snapshot,
    hub_member_connection_state_snapshot,
    media_plane_runtime_snapshot,
    reliability_snapshot,
    sidecar_runtime_snapshot,
    skill_runtime_migration_update_gate_snapshot,
    supervisor_channel_runtime_snapshot,
    yjs_sync_runtime_snapshot,
)
from adaos.services.reliability_runtime_beacon import run_reliability_runtime_beacon
from adaos.services.operations import submit_marketplace_install_action
from adaos.services.runtime_topology import supervisor_base_from_env
from adaos.services.scenario.webspace_runtime import (
    WebspaceService,
    describe_webspace_operational_state,
    describe_webspace_validation_state,
    describe_webspace_overlay_state,
    describe_webspace_projection_state,
    describe_webspace_rebuild_state,
    ensure_dev_webspace_for_scenario,
    get_webspace_rebuild_materialized_payload,
    go_home_webspace,
    reload_webspace_from_scenario,
    restore_webspace_from_snapshot,
    set_current_webspace_home,
    switch_webspace_scenario,
)
from adaos.services.workspaces import index as workspace_index
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.runtime import SkillDirectoryNotFoundError, find_skill_dir
from adaos.services.realtime_sidecar import (
    realtime_sidecar_listener_snapshot,
    restart_realtime_sidecar_subprocess,
)
from adaos.services.root_mcp.logs import list_local_logs, normalize_log_category
from adaos.services.ui_runtime_diagnostics import ingest_ui_runtime_diagnostics
from adaos.services.webui_contract import webui_contract_diagnostic_catalog
from adaos.services.runtime_lifecycle import runtime_lifecycle_snapshot
from adaos.services.system_model.service import (
    compact_node_status_transport_payload,
    current_inventory_projection,
    current_neighborhood_projection,
    current_node_object,
    current_node_probe_status_payload,
    current_node_status_payload,
    current_object_inspector,
    current_object_projection,
    current_overview_projection,
    current_reliability_payload,
    current_reliability_projection,
    current_subnet_planning_context,
    current_task_packet,
    current_topology_projection,
    route_info,
)
from adaos.services.system_model.projections import compact_overview_projection_dict
from adaos.services.status.guard_cards import guard_status_cards_from_runtime
from adaos.services.incident_registry import (
    incident_registry_snapshot,
    is_yjs_thread_affinity_fault,
    record_yjs_thread_affinity_fault,
)
from adaos.services.projection_demand import (
    delete_client_subscription_record,
    projection_demand_snapshot,
    resolve_projection_demand_stale_after_s,
    touch_client_subscription_record,
    write_client_subscription_record,
)
from adaos.services.projection_demand_mapper import (
    browser_surface_lifecycle_contract_snapshot,
    build_browser_projection_demand_record,
)
from adaos.services.projection_demand_restore import projection_demand_restore_contract_snapshot
from adaos.services.projection_demand_yjs import (
    materialize_projection_demand_to_yjs,
    read_projection_demand_yjs,
    restore_projection_demand_from_yjs,
    safe_materialize_projection_demand_to_yjs,
)
from adaos.services.projection_diagnostics import projection_operator_diagnostics
from adaos.services.projection_dispatcher import (
    core_skill_refresh_contract_snapshot,
    dispatch_demanded_projection_refresh,
    projection_dispatcher_memory_contract_snapshot,
    projection_dispatcher_snapshot,
)
from adaos.services.projection_event_bridge import projection_event_bridge_snapshot
from adaos.services.projection_pilot_readiness import projection_pilot_readiness_contract_snapshot
from adaos.services.projection_record_yjs import (
    materialize_projection_records_to_yjs,
    normalize_projection_record_keys,
    projection_records_node_multiplicity_contract_snapshot,
    read_projection_records_yjs_cache,
)
from adaos.services.projection_records import (
    browser_projection_adapter_contract_snapshot,
    browser_projection_record_snapshot,
    get_projection_record,
    projection_record_registry_snapshot,
)
from adaos.services.projection_runtime_ownership import projection_runtime_ownership_contract_snapshot
from adaos.services.platform_node_yjs import (
    materialize_platform_node_to_yjs,
    platform_nodes_contract_snapshot,
    read_platform_nodes_yjs,
)
from adaos.services.status_projection import (
    ensure_status_card_projection_handler,
    materialize_status_card_projection_records,
    platform_emitter_contract_snapshot,
)
from adaos.services.yjs.doc import async_read_ydoc
from adaos.services.yjs.store import get_ystore_for_webspace
from adaos.services.yjs.webspace import coerce_webspace_id, default_webspace_id

router = APIRouter()
_log = logging.getLogger("adaos.api.node_api")

_RELIABILITY_SUMMARY_METRICS_LOCK = threading.RLock()
_RELIABILITY_SUMMARY_METRICS: dict[str, Any] = {
    "schema": "adaos.reliability_summary.metrics.v1",
    "started_at": time.time(),
    "updated_at": None,
    "total": {
        "response_total": 0,
        "not_modified_total": 0,
        "body_bytes_total": 0,
    },
    "modes": {},
}
_RUNTIME_ENDPOINT_METRICS_LOCK = threading.Lock()
_RUNTIME_ENDPOINT_METRICS: dict[str, Any] = {
    "schema": "adaos.runtime_endpoint.metrics.v1",
    "started_at": time.time(),
    "updated_at": None,
    "slow_threshold_ms": 1000.0,
    "endpoints": {},
}


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)) or str(default))
    except Exception:
        value = float(default)
    return max(float(minimum), value)


_YJS_MATERIALIZATION_SNAPSHOT_TIMEOUT_S = _env_float(
    "ADAOS_YJS_MATERIALIZATION_SNAPSHOT_TIMEOUT_S",
    2.5,
    minimum=0.1,
)
_BROWSER_RESOURCE_MAX_BYTES = int(
    _env_float(
        "ADAOS_BROWSER_RESOURCE_MAX_BYTES",
        5 * 1024 * 1024,
        minimum=1024,
    )
)


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    cloned = _clone_json_like(value)
    return dict(cloned) if isinstance(cloned, dict) else {}


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    cloned = _clone_json_like(value)
    return list(cloned) if isinstance(cloned, list) else []


def _clone_json_like(value: Any) -> Any:
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            raw = to_json()
            if isinstance(raw, str):
                return json.loads(raw)
            return json.loads(json.dumps(raw))
        except Exception as exc:
            _record_yjs_clone_fault(exc, operation="to_json")
            pass
    try:
        return json.loads(json.dumps(value))
    except Exception as exc:
        _record_yjs_clone_fault(exc, operation="json_clone")
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(k): _clone_json_like(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_clone_json_like(v) for v in value]
        if isinstance(value, tuple):
            return [_clone_json_like(v) for v in value]
        if isinstance(value, Mapping):
            return {str(k): _clone_json_like(v) for k, v in value.items()}
        items = getattr(value, "items", None)
        if callable(items):
            try:
                return {str(k): _clone_json_like(v) for k, v in items() if str(k)}
            except Exception as exc:
                _record_yjs_clone_fault(exc, operation="items")
                return {}
        if hasattr(value, "__iter__") and not isinstance(value, (str, bytes, bytearray)):
            try:
                return [_clone_json_like(v) for v in value]
            except Exception as exc:
                _record_yjs_clone_fault(exc, operation="iter")
                return []
        return str(value)


def _record_yjs_clone_fault(exc: BaseException, *, operation: str) -> None:
    try:
        if is_yjs_thread_affinity_fault(exc):
            record_yjs_thread_affinity_fault(
                source="api.node.reliability.summary",
                component="json_clone",
                operation=operation,
                exc=exc,
            )
    except Exception:
        pass


def _coerce_node_webspace_id(value: Any = None) -> str:
    return coerce_webspace_id(value, fallback=default_webspace_id())


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _local_node_id() -> str:
    try:
        conf = load_config()
        node_id = str(getattr(conf, "node_id", "") or "").strip()
        if node_id:
            return node_id
        nested = str(getattr(getattr(conf, "node_settings", None), "id", "") or "").strip()
        if nested:
            return nested
    except Exception:
        pass
    return "hub"


def _local_node_label() -> str:
    try:
        conf = load_config()
        return str(node_display_from_config(conf).get("node_label") or "").strip() or _local_node_id()
    except Exception:
        return _local_node_id()


def _local_node_display() -> dict[str, Any]:
    try:
        return node_display_from_config(load_config())
    except Exception:
        return {
            "node_label": _local_node_label(),
            "node_compact_label": "N0",
            "node_index": 0,
            "node_color": "",
            "node_color_index": 0,
        }



def _read_node_scoped_scenario_entry(scenarios_root: Any, scenario_id: str, *, node_id: str | None = None) -> dict[str, Any]:
    root = _coerce_dict(scenarios_root or {})
    target_node_id = str(node_id or "").strip() or _local_node_id()
    local_bucket = _coerce_dict(root.get(target_node_id) or {})
    local_entry = _coerce_dict(local_bucket.get(scenario_id) or {})
    if local_entry:
        return local_entry
    for maybe_bucket in root.values():
        bucket = _coerce_dict(maybe_bucket or {})
        entry = _coerce_dict(bucket.get(scenario_id) or {})
        if entry:
            return entry
    return {}


async def _current_reliability_payload_async(*, webspace_id: str | None = None) -> dict[str, Any]:
    if webspace_id is None:
        return await anyio.to_thread.run_sync(current_reliability_payload)
    return await anyio.to_thread.run_sync(partial(current_reliability_payload, webspace_id=webspace_id))


def _current_status_registry_snapshot(
    *,
    webspace_id: str | None = None,
    owner: str | None = None,
    scope: str | None = None,
    include_stale: bool = True,
) -> dict[str, Any]:
    now = time.time()
    try:
        registry = get_ctx().status_registry
        snapshot = registry.snapshot(
            webspace_id=webspace_id,
            owner=owner,
            scope=scope,
            include_stale=include_stale,
            now_ts=now,
        )
        snapshot["available"] = True
        return snapshot
    except Exception as exc:
        return {
            "schema": "adaos.status_registry.v1",
            "available": False,
            "updated_at": now,
            "cards": [],
            "total": 0,
            "diagnostics": {
                "schema": "adaos.status_registry.diagnostics.v1",
                "card_count": 0,
                "publish_total": 0,
                "changed_total": 0,
                "unchanged_total": 0,
                "stale_count": 0,
                "last_publish_latency_ms": 0.0,
                "last_changed_at": None,
            },
            "error": f"{type(exc).__name__}: {exc}",
        }


def _compact_status_card(value: Any) -> dict[str, Any]:
    card = _coerce_dict(value)
    return {
        "id": str(card.get("id") or "").strip() or "unknown",
        "owner": str(card.get("owner") or "").strip() or "unknown",
        "kind": str(card.get("kind") or "").strip() or "status",
        "scope": str(card.get("scope") or "").strip() or "runtime",
        "status": str(card.get("status") or "unknown").strip() or "unknown",
        "summary": str(card.get("summary") or "").strip() or None,
        "severity": str(card.get("severity") or "unknown").strip() or "unknown",
        "webspaceId": str(card.get("webspace_id") or "").strip() or None,
        "updatedAt": card.get("updated_at"),
        "ttlMs": _coerce_optional_int(card.get("ttl_ms")),
        "stale": bool(card.get("stale")),
        "version": int(card.get("version") or 1),
        "fingerprint": str(card.get("fingerprint") or "").strip() or None,
        "changedAt": card.get("changed_at"),
        "incidentId": str(card.get("incident_id") or "").strip() or None,
        "detailsRef": _coerce_dict(card.get("details_ref")),
        "route": _coerce_dict(card.get("route")),
        "guardRef": _coerce_dict(card.get("guard_ref")),
    }


def _status_card_key(card: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(card.get("scope") or "").strip(),
        str(card.get("owner") or "").strip(),
        str(card.get("webspace_id") or "").strip(),
        str(card.get("id") or "").strip(),
    )


def _with_derived_status_cards(snapshot: dict[str, Any], cards: list[Any]) -> dict[str, Any]:
    if not cards:
        return snapshot
    merged = dict(snapshot)
    rows = [dict(item) for item in _coerce_list(snapshot.get("cards")) if isinstance(item, dict)]
    seen = {_status_card_key(row) for row in rows}
    derived_rows: list[dict[str, Any]] = []
    for card in cards:
        payload = card.to_dict() if hasattr(card, "to_dict") else _coerce_dict(card)
        if not payload:
            continue
        key = _status_card_key(payload)
        if key in seen:
            continue
        seen.add(key)
        rows.append(payload)
        derived_rows.append(payload)
    diagnostics = _coerce_dict(snapshot.get("diagnostics"))
    diagnostics["derived_card_count"] = int(diagnostics.get("derived_card_count") or 0) + len(derived_rows)
    merged["cards"] = rows
    merged["total"] = len(rows)
    merged["diagnostics"] = diagnostics
    return merged


def _compact_status_registry_payload(
    snapshot: dict[str, Any],
    *,
    webspace_id: str | None = None,
    limit: int | None = 50,
    source: str = "api.node.status.cards",
) -> dict[str, Any]:
    diagnostics = _coerce_dict(snapshot.get("diagnostics"))
    cards = [_compact_status_card(card) for card in _coerce_list(snapshot.get("cards")) if isinstance(card, dict)]
    limit_value = max(0, min(int(limit if limit is not None else 50), 500))
    cards = cards[:limit_value]
    return {
        "ok": True,
        "available": bool(snapshot.get("available", True)),
        "schema": str(snapshot.get("schema") or "adaos.status_registry.v1"),
        "source": source,
        "webspaceId": str(webspace_id or "").strip() or None,
        "updatedAt": int(float(snapshot.get("updated_at") or time.time()) * 1000),
        "total": int(snapshot.get("total") or len(cards)),
        "returned": len(cards),
        "diagnostics": {
            "cardCount": int(diagnostics.get("card_count") or 0),
            "publishTotal": int(diagnostics.get("publish_total") or 0),
            "changedTotal": int(diagnostics.get("changed_total") or 0),
            "unchangedTotal": int(diagnostics.get("unchanged_total") or 0),
            "maxCardBytes": _coerce_optional_int(diagnostics.get("max_card_bytes")),
            "maxCardBytesObserved": int(diagnostics.get("max_card_bytes_observed") or 0),
            "oversizedCardTotal": int(diagnostics.get("oversized_card_total") or 0),
            "lastOversizedCard": _coerce_dict(diagnostics.get("last_oversized_card")),
            "staleCount": int(diagnostics.get("stale_count") or 0),
            "derivedCardCount": int(diagnostics.get("derived_card_count") or 0),
            "lastPublishLatencyMs": float(diagnostics.get("last_publish_latency_ms") or 0.0),
            "lastChangedAt": diagnostics.get("last_changed_at"),
        },
        "cards": cards,
        "error": str(snapshot.get("error") or "").strip() or None,
    }


def _strip_summary_etag_volatiles(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_summary_etag_volatiles(item)
            for key, item in value.items()
            if str(key)
            not in {
                "age_s",
                "expires_at",
                "updated_at",
                "updatedAt",
                "changedAt",
                "lastGoodSyncAt",
                "lastMaterializationAt",
                "lastPublishLatencyMs",
                "maxCardBytesObserved",
            }
        }
    if isinstance(value, list):
        return [_strip_summary_etag_volatiles(item) for item in value]
    return value


def _summary_etag(payload: Mapping[str, Any]) -> str:
    stable = _strip_summary_etag_volatiles(payload)
    raw = json.dumps(stable, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return f'W/"{hashlib.sha1(raw.encode("utf-8")).hexdigest()}"'


def _etag_matches(header: str | None, etag: str) -> bool:
    tokens = [item.strip() for item in str(header or "").split(",") if item.strip()]
    return "*" in tokens or etag in tokens


def _summary_body_size(payload: Mapping[str, Any]) -> int:
    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        return len(raw.encode("utf-8"))
    except Exception:
        return 0


def _json_response_body(payload: Any) -> bytes:
    """Encode a JSON response once; callers may run this helper off-loop."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        indent=None,
        separators=(",", ":"),
    ).encode("utf-8")


def _runtime_endpoint_slow_threshold_ms() -> float:
    return _env_float("ADAOS_RUNTIME_ENDPOINT_SLOW_MS", 1000.0, minimum=1.0)


def _record_runtime_endpoint_metric(
    *,
    endpoint: str,
    duration_ms: float,
    status_code: int,
    body_bytes: int,
    error: str | None = None,
) -> None:
    now = time.time()
    endpoint_id = str(endpoint or "unknown").strip() or "unknown"
    duration = max(0.0, float(duration_ms or 0.0))
    body_size = max(0, int(body_bytes or 0))
    slow_threshold = _runtime_endpoint_slow_threshold_ms()
    with _RUNTIME_ENDPOINT_METRICS_LOCK:
        endpoints = _coerce_dict(_RUNTIME_ENDPOINT_METRICS.get("endpoints"))
        row = _coerce_dict(endpoints.get(endpoint_id))
        row["response_total"] = int(row.get("response_total") or 0) + 1
        row["error_total"] = int(row.get("error_total") or 0) + (1 if error else 0)
        row["slow_total"] = int(row.get("slow_total") or 0) + (1 if duration >= slow_threshold else 0)
        row["body_bytes_total"] = int(row.get("body_bytes_total") or 0) + body_size
        row["last_status_code"] = int(status_code)
        row["last_duration_ms"] = round(duration, 3)
        row["last_body_bytes"] = body_size
        row["last_error"] = str(error or "").strip() or None
        row["last_at"] = now
        row["max_duration_ms"] = round(max(float(row.get("max_duration_ms") or 0.0), duration), 3)
        row["max_body_bytes"] = max(int(row.get("max_body_bytes") or 0), body_size)
        if duration >= slow_threshold:
            row["last_slow_at"] = now
            row["last_slow_duration_ms"] = round(duration, 3)
            row["last_slow_body_bytes"] = body_size
        endpoints[endpoint_id] = row
        _RUNTIME_ENDPOINT_METRICS["slow_threshold_ms"] = slow_threshold
        _RUNTIME_ENDPOINT_METRICS["endpoints"] = endpoints
        _RUNTIME_ENDPOINT_METRICS["updated_at"] = now


def _runtime_endpoint_metrics_snapshot() -> dict[str, Any]:
    with _RUNTIME_ENDPOINT_METRICS_LOCK:
        return json.loads(json.dumps(_RUNTIME_ENDPOINT_METRICS, ensure_ascii=True, default=str))


def _record_reliability_summary_metric(
    *,
    mode: str,
    status_code: int,
    body_bytes: int,
    cache_hit: bool,
    etag: str,
) -> None:
    now = time.time()
    mode_id = str(mode or "unknown").strip() or "unknown"
    with _RELIABILITY_SUMMARY_METRICS_LOCK:
        total = _coerce_dict(_RELIABILITY_SUMMARY_METRICS.get("total"))
        total["response_total"] = int(total.get("response_total") or 0) + 1
        total["not_modified_total"] = int(total.get("not_modified_total") or 0) + (1 if status_code == 304 else 0)
        total["body_bytes_total"] = int(total.get("body_bytes_total") or 0) + max(0, int(body_bytes or 0))
        modes = _coerce_dict(_RELIABILITY_SUMMARY_METRICS.get("modes"))
        row = _coerce_dict(modes.get(mode_id))
        row["response_total"] = int(row.get("response_total") or 0) + 1
        row["not_modified_total"] = int(row.get("not_modified_total") or 0) + (1 if status_code == 304 else 0)
        row["body_bytes_total"] = int(row.get("body_bytes_total") or 0) + max(0, int(body_bytes or 0))
        row["last_status_code"] = int(status_code)
        row["last_body_bytes"] = max(0, int(body_bytes or 0))
        row["last_cache_hit"] = bool(cache_hit)
        row["last_etag"] = str(etag or "").strip() or None
        row["last_at"] = now
        modes[mode_id] = row
        _RELIABILITY_SUMMARY_METRICS["total"] = total
        _RELIABILITY_SUMMARY_METRICS["modes"] = modes
        _RELIABILITY_SUMMARY_METRICS["updated_at"] = now


def _compact_status_registry_metrics(snapshot: dict[str, Any], *, webspace_id: str | None = None) -> dict[str, Any]:
    diagnostics = _coerce_dict(snapshot.get("diagnostics"))
    return {
        "schema": "adaos.status_registry.acceptance_metrics.v1",
        "available": bool(snapshot.get("available", True)),
        "webspace_id": str(webspace_id or "").strip() or None,
        "total": int(snapshot.get("total") or 0),
        "diagnostics": {
            "card_count": int(diagnostics.get("card_count") or 0),
            "publish_total": int(diagnostics.get("publish_total") or 0),
            "changed_total": int(diagnostics.get("changed_total") or 0),
            "unchanged_total": int(diagnostics.get("unchanged_total") or 0),
            "stale_count": int(diagnostics.get("stale_count") or 0),
            "max_card_bytes": _coerce_optional_int(diagnostics.get("max_card_bytes")),
            "max_card_bytes_observed": int(diagnostics.get("max_card_bytes_observed") or 0),
            "oversized_card_total": int(diagnostics.get("oversized_card_total") or 0),
            "last_oversized_card": _coerce_dict(diagnostics.get("last_oversized_card")),
            "last_publish_latency_ms": float(diagnostics.get("last_publish_latency_ms") or 0.0),
            "last_changed_at": diagnostics.get("last_changed_at"),
        },
        "error": str(snapshot.get("error") or "").strip() or None,
    }


def _current_webio_stream_guard_metrics(
    *,
    webspace_id: str | None = None,
    receiver: str | None = None,
    owner: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    try:
        from adaos.services.router.service import webio_stream_guard_snapshot

        payload = webio_stream_guard_snapshot(
            webspace_id=webspace_id,
            receiver=receiver,
            owner=owner,
            limit=limit,
        )
        result = dict(payload) if isinstance(payload, dict) else {}
        result["available"] = True
        result.setdefault("items", [])
        result.setdefault("totals", {})
        return result
    except Exception as exc:
        return {
            "schema": "adaos.webio_stream_guard.v1",
            "available": False,
            "webspace_id": str(webspace_id or "").strip() or None,
            "receiver": str(receiver or "").strip() or None,
            "owner": str(owner or "").strip() or None,
            "items": [],
            "total": 0,
            "totals": {
                "attempted": 0,
                "published": 0,
                "suppressed": 0,
                "throttled": 0,
                "published_fanout": 0,
            },
            "error": f"{type(exc).__name__}: {exc}",
        }


def _compact_webio_stream_guard_metrics(payload: dict[str, Any], *, limit: int = 20) -> dict[str, Any]:
    totals = _coerce_dict(payload.get("totals"))
    rows = [
        {
            "webspace_id": str(row.get("webspace_id") or "").strip() or None,
            "receiver": str(row.get("receiver") or "").strip() or None,
            "owner": str(row.get("owner") or "").strip() or None,
            "surface": str(row.get("surface") or "").strip() or None,
            "attempted": int(row.get("attempted_total") or 0),
            "published": int(row.get("published_total") or 0),
            "suppressed": int(row.get("suppressed_total") or 0),
            "throttled": int(row.get("throttled_total") or 0),
            "published_fanout": int(row.get("published_fanout_total") or 0),
            "last_fanout": int(row.get("last_fanout_total") or 0),
            "last_payload_bytes": int(row.get("last_payload_bytes") or 0),
            "last_effective_bytes": int(row.get("last_effective_bytes") or 0),
            "declared_max_payload_bytes": _coerce_optional_int(row.get("declared_max_payload_bytes")),
            "last_policy_state": str(row.get("last_policy_state") or "").strip() or None,
            "last_reason": str(row.get("last_reason") or "").strip() or None,
            "last_at": row.get("last_at"),
        }
        for row in _coerce_list(payload.get("items"))[: max(0, min(int(limit or 20), 100))]
        if isinstance(row, dict)
    ]
    return {
        "schema": "adaos.webio_stream_guard.acceptance_metrics.v1",
        "available": bool(payload.get("available", True)),
        "webspace_id": str(payload.get("webspace_id") or "").strip() or None,
        "receiver": str(payload.get("receiver") or "").strip() or None,
        "owner": str(payload.get("owner") or "").strip() or None,
        "total": int(payload.get("total") or len(rows)),
        "totals": {
            "attempted": int(totals.get("attempted") or 0),
            "published": int(totals.get("published") or 0),
            "suppressed": int(totals.get("suppressed") or 0),
            "throttled": int(totals.get("throttled") or 0),
            "published_fanout": int(totals.get("published_fanout") or 0),
        },
        "items": rows,
        "error": str(payload.get("error") or "").strip() or None,
    }


def _current_yjs_owner_guard_metrics(
    *,
    webspace_id: str | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    try:
        from adaos.services.yjs.governance import primary_doc_governance_snapshot

        payload = primary_doc_governance_snapshot(webspace_id=webspace_id, owner=owner)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _compact_yjs_owner_guard_metrics(
    payload: dict[str, Any],
    *,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    owner_guard = _coerce_dict(data.get("owner_guard"))
    active_quarantines = _coerce_list(owner_guard.get("active_quarantines"))
    remaining = data.get("quarantine_remaining_s")
    return {
        "schema": "adaos.yjs_owner_guard.acceptance_metrics.v1",
        "available": bool(data.get("available", bool(data))) and bool(data.get("enabled", True)),
        "webspace_id": str(data.get("webspace_id") or webspace_id or "").strip() or None,
        "owner": str(data.get("owner") or "").strip() or None,
        "attempted": int(data.get("attempted_total") or 0),
        "allowed": int(data.get("allowed_total") or 0),
        "blocked": int(data.get("blocked_total") or 0),
        "throttled": int(data.get("throttled_total") or 0),
        "quarantined": bool(data.get("quarantined")),
        "quarantine_enabled": bool(data.get("quarantine_enabled")),
        "quarantine_total": int(data.get("quarantine_total") or 0),
        "quarantine_denied_total": int(data.get("quarantine_denied_total") or 0),
        "active_quarantine_total": len(active_quarantines),
        "quarantine_remaining_s": round(float(remaining or 0.0), 3) if remaining is not None else None,
        "quarantine_reason": str(data.get("quarantine_reason") or "").strip() or None,
        "quarantine_trigger": str(data.get("quarantine_trigger") or "").strip() or None,
        "quarantine_path": str(data.get("quarantine_path") or "").strip() or None,
        "quarantine_tool": str(data.get("quarantine_tool") or "").strip() or None,
        "last_decision": str(data.get("last_decision") or "").strip() or None,
        "last_policy_state": str(data.get("last_policy_state") or "").strip() or None,
        "last_reason": str(data.get("last_reason") or "").strip() or None,
        "last_path": str(data.get("last_path") or "").strip() or None,
        "last_source": str(data.get("last_source") or "").strip() or None,
        "last_channel": str(data.get("last_channel") or "").strip() or None,
        "last_update_bytes": int(data.get("last_update_bytes") or 0),
        "error": str(data.get("error") or "").strip() or None,
    }


def _current_eventbus_backlog_metrics() -> dict[str, Any]:
    try:
        bus = getattr(get_ctx(), "bus", None)
        snapshot_fn = getattr(bus, "backlog_snapshot", None)
        if callable(snapshot_fn):
            payload = snapshot_fn()
            result = dict(payload) if isinstance(payload, dict) else {}
            result["available"] = True
            result.setdefault("top_webio_stream_controls", [])
            return result
    except Exception as exc:
        return {
            "available": False,
            "top_webio_stream_controls": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "available": False,
        "top_webio_stream_controls": [],
    }


def _compact_webio_stream_control_metrics(
    backlog: dict[str, Any],
    *,
    webspace_id: str | None = None,
    receiver: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    token_ws = str(webspace_id or "").strip()
    token_receiver = str(receiver or "").strip()
    rows: list[dict[str, Any]] = []
    for raw in _coerce_list(backlog.get("top_webio_stream_controls")):
        if not isinstance(raw, dict):
            continue
        row_ws = str(raw.get("webspace_id") or "").strip()
        row_receiver = str(raw.get("receiver") or "").strip()
        if token_ws and row_ws != token_ws:
            continue
        if token_receiver and row_receiver != token_receiver:
            continue
        event_type = str(raw.get("event_type") or "").strip()
        incoming = int(raw.get("incoming_total") or 0)
        superseded = int(raw.get("superseded_total") or 0)
        rows.append(
            {
                "event_type": event_type or None,
                "webspace_id": row_ws or None,
                "target_node_id": str(raw.get("target_node_id") or "").strip() or None,
                "receiver": row_receiver or None,
                "source": str(raw.get("source") or "").strip() or None,
                "incoming": incoming,
                "snapshot_requested": incoming if event_type == "webio.stream.snapshot.requested" else 0,
                "queued": int(raw.get("queued_total") or 0),
                "prefiltered": int(raw.get("prefiltered_total") or 0),
                "coalesced": superseded,
                "superseded": superseded,
                "dropped": int(raw.get("dropped_total") or 0),
                "last_action": str(raw.get("last_action") or "").strip() or None,
                "last_handler": str(raw.get("last_handler") or "").strip() or None,
                "last_at": raw.get("last_at"),
            }
        )
    rows.sort(
        key=lambda item: (
            -int(item.get("coalesced") or 0),
            -int(item.get("dropped") or 0),
            -int(item.get("snapshot_requested") or 0),
            str(item.get("receiver") or ""),
        )
    )
    max_items = max(0, min(int(limit or 20), 100))
    limited = rows[:max_items]
    return {
        "schema": "adaos.webio_stream_control.acceptance_metrics.v1",
        "available": bool(backlog.get("available")),
        "webspace_id": token_ws or None,
        "receiver": token_receiver or None,
        "pending_tasks": int(backlog.get("pending_tasks") or 0),
        "pending_peak": int(backlog.get("pending_peak") or 0),
        "bounded_queue_total": int(backlog.get("bounded_queue_total") or 0),
        "bounded_queue_peak": int(backlog.get("bounded_queue_peak") or 0),
        "bounded_active_workers": int(backlog.get("bounded_active_workers") or 0),
        "totals": {
            "incoming": sum(int(item.get("incoming") or 0) for item in rows),
            "snapshot_requested": sum(int(item.get("snapshot_requested") or 0) for item in rows),
            "queued": sum(int(item.get("queued") or 0) for item in rows),
            "prefiltered": sum(int(item.get("prefiltered") or 0) for item in rows),
            "coalesced": sum(int(item.get("coalesced") or 0) for item in rows),
            "superseded": sum(int(item.get("superseded") or 0) for item in rows),
            "dropped": sum(int(item.get("dropped") or 0) for item in rows),
        },
        "items": limited,
        "error": str(backlog.get("error") or "").strip() or None,
    }


def _stream_receiver_acceptance_metrics(
    *,
    stream_guard: dict[str, Any],
    stream_controls: dict[str, Any],
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def _receiver_row(webspace_id: Any, receiver: Any) -> dict[str, Any]:
        key = (
            str(webspace_id or "").strip(),
            str(receiver or "").strip(),
        )
        if key not in rows:
            rows[key] = {
                "webspace_id": key[0] or None,
                "receiver": key[1] or None,
                "owner": None,
                "surface": None,
                "attempted": 0,
                "published": 0,
                "suppressed": 0,
                "throttled": 0,
                "published_fanout": 0,
                "snapshot_requested": 0,
                "queued": 0,
                "coalesced": 0,
                "superseded": 0,
                "dropped": 0,
            }
        return rows[key]

    for item in _coerce_list(stream_guard.get("items")):
        if not isinstance(item, dict):
            continue
        row = _receiver_row(item.get("webspace_id"), item.get("receiver"))
        row["owner"] = row.get("owner") or item.get("owner")
        row["surface"] = row.get("surface") or item.get("surface")
        for field in ("attempted", "published", "suppressed", "throttled", "published_fanout"):
            row[field] = int(row.get(field) or 0) + int(item.get(field) or 0)

    for item in _coerce_list(stream_controls.get("items")):
        if not isinstance(item, dict):
            continue
        row = _receiver_row(item.get("webspace_id"), item.get("receiver"))
        for field in ("snapshot_requested", "queued", "coalesced", "superseded", "dropped"):
            row[field] = int(row.get(field) or 0) + int(item.get(field) or 0)

    result = list(rows.values())
    result.sort(
        key=lambda item: (
            -int(item.get("suppressed") or 0),
            -int(item.get("coalesced") or 0),
            -int(item.get("snapshot_requested") or 0),
            -int(item.get("published_fanout") or 0),
            str(item.get("receiver") or ""),
        )
    )
    return result[: max(0, min(int(limit or 20), 100))]


def _acceptance_observability_metrics(
    *,
    webspace_id: str | None = None,
    receiver: str | None = None,
    owner: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    resolved_webspace_id = _coerce_node_webspace_id(webspace_id) if webspace_id is not None else None
    max_items = max(1, min(int(limit or 20), 100))
    status_registry = _compact_status_registry_metrics(
        _current_status_registry_snapshot(webspace_id=resolved_webspace_id),
        webspace_id=resolved_webspace_id,
    )
    stream_guard = _compact_webio_stream_guard_metrics(
        _current_webio_stream_guard_metrics(
            webspace_id=resolved_webspace_id,
            receiver=receiver,
            owner=owner,
            limit=max_items,
        ),
        limit=max_items,
    )
    yjs_guard = _compact_yjs_owner_guard_metrics(
        _current_yjs_owner_guard_metrics(
            webspace_id=resolved_webspace_id,
            owner=owner,
        ),
        webspace_id=resolved_webspace_id,
    )
    stream_controls = _compact_webio_stream_control_metrics(
        _current_eventbus_backlog_metrics(),
        webspace_id=resolved_webspace_id,
        receiver=receiver,
        limit=max_items,
    )
    return {
        "schema": "adaos.reliability_summary.acceptance_metrics.v1",
        "webspace_id": resolved_webspace_id,
        "receiver": str(receiver or "").strip() or None,
        "owner": str(owner or "").strip() or None,
        "status_registry": status_registry,
        "yjs_guard": yjs_guard,
        "stream_guard": stream_guard,
        "stream_controls": stream_controls,
        "stream_receivers": _stream_receiver_acceptance_metrics(
            stream_guard=stream_guard,
            stream_controls=stream_controls,
            limit=max_items,
        ),
        "notes": {
            "unchanged_source": "status_registry.unchanged_total and summary not_modified_total; router cannot observe skill-side unchanged stream dedupe unless the skill publishes that diagnostic",
            "coalesced_source": "eventbus bounded superseded controls are reported as coalesced for soak readability",
            "yjs_guard_source": "primary-doc governance and owner quarantine counters; observability only, not a data-route replacement",
        },
    }


def _reliability_summary_metrics_snapshot(
    *,
    webspace_id: str | None = None,
    receiver: str | None = None,
    owner: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    with _RELIABILITY_SUMMARY_METRICS_LOCK:
        payload = json.loads(json.dumps(_RELIABILITY_SUMMARY_METRICS, ensure_ascii=True, default=str))
    payload["acceptance"] = _acceptance_observability_metrics(
        webspace_id=webspace_id,
        receiver=receiver,
        owner=owner,
        limit=limit,
    )
    payload["runtime_endpoints"] = _runtime_endpoint_metrics_snapshot()
    return payload


def _json_response_with_etag(
    payload: dict[str, Any],
    *,
    if_none_match: str | None = None,
    mode: str,
    started_at: float | None = None,
    endpoint: str | None = None,
) -> Response:
    etag = _summary_etag(payload)
    cache_hit = _etag_matches(if_none_match, etag)
    body_bytes = 0 if cache_hit else _summary_body_size(payload)
    duration_ms = max(0.0, (time.time() - float(started_at or time.time())) * 1000.0)
    headers = {
        "Cache-Control": "no-cache",
        "ETag": etag,
        "X-AdaOS-Summary-Mode": mode,
        "X-AdaOS-Summary-Cache": "hit" if cache_hit else "miss",
        "X-AdaOS-Summary-Body-Bytes": str(body_bytes),
        "X-AdaOS-Runtime-Duration-Ms": str(round(duration_ms, 3)),
    }
    _record_reliability_summary_metric(
        mode=mode,
        status_code=304 if cache_hit else 200,
        body_bytes=body_bytes,
        cache_hit=cache_hit,
        etag=etag,
    )
    _record_runtime_endpoint_metric(
        endpoint=str(endpoint or f"/api/node/reliability/summary:{mode}"),
        duration_ms=duration_ms,
        status_code=304 if cache_hit else 200,
        body_bytes=body_bytes,
    )
    _log.debug(
        "reliability summary response mode=%s status=%s bytes=%s cache=%s duration_ms=%.3f",
        mode,
        304 if cache_hit else 200,
        body_bytes,
        "hit" if cache_hit else "miss",
        duration_ms,
    )
    if cache_hit:
        return Response(status_code=304, headers=headers)
    return JSONResponse(content=payload, headers=headers)


def _runtime_reliability_observer(role: str | None) -> dict[str, Any]:
    role = str(role or "unknown").strip().lower() or "unknown"
    domain = {
        "root": "root_browser",
        "hub": "hub_browser",
        "member": "member_browser",
    }.get(role, "node_browser")
    all_domains = {"root_browser", "hub_root", "hub_browser", "browser_hub_direct"}
    return {
        "schema": "adaos.runtime_observer.v1",
        "domain": domain,
        "role": role,
        "nodeId": None,
        "authority": "local_runtime_only",
        "doesNotImply": sorted(all_domains - {domain}),
    }


def _current_compact_member_availability() -> dict[str, Any]:
    try:
        conf = load_config()
        role = str(getattr(conf, "role", "") or "").strip().lower()
        route_mode, connected = route_info(role)
        snapshot = hub_member_connection_state_snapshot(
            role=role,
            route_mode=route_mode,
            connected_to_hub=connected,
            node_id=str(getattr(conf, "node_id", "") or "").strip(),
            node_names=list(getattr(conf, "node_names", []) or []),
        )
        return _compact_member_availability(snapshot)
    except Exception:
        return _compact_member_availability({})


def _thin_runtime_reliability_payload(
    status_registry: dict[str, Any] | None,
    *,
    webspace_id: str | None = None,
    mode: str = "thin",
) -> dict[str, Any]:
    resolved_webspace_id = _coerce_node_webspace_id(webspace_id)
    requested_mode = str(mode or "thin").strip().lower()
    include_status_plane = requested_mode in {"thin", "details"}
    incidents = _current_incident_registry_snapshot()
    runtime_fault = _runtime_fault_from_incidents(incidents)
    if include_status_plane and str(runtime_fault.get("state") or "").strip().lower() == "degraded":
        status_registry = _with_derived_status_cards(
            status_registry or {},
            guard_status_cards_from_runtime(
                {"incident_registry": incidents},
                webspace_id=resolved_webspace_id,
            ),
        )
    status_plane: dict[str, Any] | None = None
    if include_status_plane:
        status_plane = _compact_status_registry_payload(
            status_registry or {},
            webspace_id=resolved_webspace_id,
            limit=50,
            source="api.node.reliability.summary.status_plane",
        )
        diagnostics = _coerce_dict(status_plane.get("diagnostics"))
        status_plane["diagnostics"] = {
            "cardCount": int(diagnostics.get("cardCount") or 0),
            "staleCount": int(diagnostics.get("staleCount") or 0),
            "derivedCardCount": int(diagnostics.get("derivedCardCount") or 0),
            "maxCardBytes": _coerce_optional_int(diagnostics.get("maxCardBytes")),
            "maxCardBytesObserved": int(diagnostics.get("maxCardBytesObserved") or 0),
            "oversizedCardTotal": int(diagnostics.get("oversizedCardTotal") or 0),
            "lastOversizedCard": _coerce_dict(diagnostics.get("lastOversizedCard")),
            "lastChangedAt": diagnostics.get("lastChangedAt"),
        }
    sidecar_fields = _thin_sidecar_runtime_fields()
    sync_runtime: dict[str, Any] = {}
    try:
        ctx = get_ctx()
        if not hasattr(ctx, "paths"):
            raise RuntimeError("AgentContext runtime paths are not initialized")
        conf = load_config()
        sync_runtime = yjs_sync_runtime_snapshot(
            role=str(getattr(conf, "role", "") or ""),
            webspace_id=resolved_webspace_id,
        )
    except Exception as exc:
        sync_runtime = {
            "available": False,
            "selected_webspace_id": resolved_webspace_id,
            "assessment": {
                "state": "unavailable",
                "reason": f"{type(exc).__name__}: {exc}",
            },
            "transport": {},
        }
    state_sync = _state_sync_snapshot(sync_runtime)
    sidecar_enablement = _coerce_dict(sidecar_fields.get("sidecarEnablement"))
    sidecar_enabled = bool(sidecar_enablement.get("enabled"))
    ws_handoff_ready = bool(sidecar_fields.get("browserWsHandoffReady"))
    yws_handoff_ready = bool(sidecar_fields.get("browserYwsHandoffReady"))
    sidecar_transport_ready = bool(sidecar_fields.get("sidecarTransportReady"))
    ws_handoff_state = str(sidecar_fields.get("browserWsHandoffState") or "unknown").strip().lower()
    if not sidecar_enabled:
        browser_transport = "ready"
        browser_transition = "ready"
        browser_reason = "runtime_browser_route_sidecar_disabled"
        browser_blockers = []
    elif ws_handoff_ready:
        browser_transport = "ready"
        browser_transition = "ready"
        browser_reason = "sidecar_browser_route_ready"
        browser_blockers: list[str] = []
    elif ws_handoff_state in {"starting", "planned", "proxy_ready"}:
        browser_transport = "degraded"
        browser_transition = "link_starting"
        browser_reason = "sidecar_browser_route_starting"
        browser_blockers = ["browser_events_ws_handoff_not_ready"]
    else:
        browser_transport = "degraded"
        browser_transition = "degraded"
        browser_reason = "browser_events_ws_handoff_not_ready"
        browser_blockers = ["browser_events_ws_handoff_not_ready"]
    required_ready = (not sidecar_enabled) or (
        ws_handoff_ready and yws_handoff_ready and sidecar_transport_ready
    )
    required_reason = (
        "runtime_browser_route_sidecar_disabled"
        if not sidecar_enabled
        else "sidecar_browser_route_ready"
        if required_ready
        else str(sidecar_fields.get("sidecarStatusReason") or "sidecar_browser_route_starting")
    )
    required_served_by = "runtime" if not sidecar_enabled else "supervisor_sidecar"
    connectivity = {
        "requiredUpstreamLink": {
            "kind": "hub_root",
            "scopeId": None,
            "transportState": "ready" if required_ready else "degraded",
            "transitionState": "ready" if required_ready else "link_starting",
            "plannedTransition": {"active": False, "reason": None},
            "reason": required_reason,
            "blockers": (
                []
                if required_ready
                else [
                    "sidecar_transport_not_ready"
                    if ws_handoff_ready and yws_handoff_ready and not sidecar_transport_ready
                    else "browser_yjs_ws_handoff_not_ready"
                ]
            ),
            "servedBy": required_served_by,
        },
        "browserControlRoute": {
            "kind": "browser_control_route",
            "scopeId": None,
            "transportState": browser_transport,
            "transitionState": browser_transition,
            "plannedTransition": {"active": False, "reason": None},
            "reason": browser_reason,
            "blockers": browser_blockers,
            "servedBy": "runtime" if not sidecar_enabled else "supervisor_sidecar",
        },
    }
    compact_state_sync = {
        "webspaceId": str(state_sync.get("webspace_id") or resolved_webspace_id).strip() or resolved_webspace_id,
        "transportState": str(state_sync.get("transport_state") or "unknown").strip() or "unknown",
        "firstSyncState": str(state_sync.get("first_sync_state") or "unknown").strip() or "unknown",
        "semanticState": str(state_sync.get("semantic_state") or "unknown").strip() or "unknown",
        "freshnessState": str(state_sync.get("freshness_state") or "unknown").strip() or "unknown",
        "lastGoodSyncAt": state_sync.get("last_good_sync_at"),
        "lastMaterializationAt": state_sync.get("last_materialization_at"),
        "replay": _coerce_dict(state_sync.get("replay")),
        "fallbackMode": str(state_sync.get("fallback_mode") or "off").strip() or "off",
        "blockers": _coerce_list(state_sync.get("blockers")),
    }
    compact_materialization = _compact_state_sync_materialization(state_sync)
    if compact_materialization:
        compact_state_sync["materialization"] = compact_materialization
    compact_state_sync = _apply_runtime_fault_to_state_sync(compact_state_sync, runtime_fault)
    compact_webrtc_yjs = _compact_webrtc_yjs_runtime(sync_runtime)
    payload = {
        "ok": True,
        "available": bool(status_plane.get("available", True)) if status_plane else True,
        "schema": f"adaos.reliability_summary.{requested_mode}.v1",
        "source": "api.node.reliability.summary",
        "mode": requested_mode,
        "webspaceId": resolved_webspace_id,
        "observer": _runtime_reliability_observer(sidecar_enablement.get("role")),
        **sidecar_fields,
        "connectivity": connectivity,
        "stateSync": compact_state_sync,
        "runtimeFault": runtime_fault,
        "webrtcYjs": compact_webrtc_yjs,
        "hubBrowserQuality": _compact_hub_browser_quality(
            connectivity=connectivity,
            state_sync=compact_state_sync,
            webrtc_yjs=compact_webrtc_yjs,
            runtime_fault=runtime_fault,
        ),
        "detailsRef": {
            "runtimeBeacon": "/api/node/reliability/runtime",
            "summaryDetails": "/api/node/reliability/details",
            "summaryFull": "/api/node/reliability/summary?mode=full",
            "runtime": "/api/node/reliability",
        },
        "cache": {
            "etag": True,
            "ifNoneMatch": True,
        },
    }
    if status_plane is not None:
        payload["updatedAt"] = status_plane.get("updatedAt")
        payload["statusPlane"] = status_plane
    # Member availability is part of the browser's compact availability
    # contract, not merely an on-demand diagnostics detail.  Omitting it from
    # the runtime beacon forces the header to infer rollout state from the
    # eventually-consistent data.nodes projection.  In particular, a terminal
    # update can retain phase=validate in that projection and look active long
    # after the supervisor has reported success.
    if requested_mode in {"runtime", "details"}:
        payload["memberAvailability"] = _current_compact_member_availability()
    return payload


def _thin_runtime_reliability_response(
    *,
    webspace_id: str | None,
    mode: str,
    if_none_match: str | None,
    started_at: float,
    include_status_registry: bool,
    endpoint: str | None = None,
) -> Response:
    resolved_webspace_id = _coerce_node_webspace_id(webspace_id)
    status_registry = (
        _current_status_registry_snapshot(webspace_id=resolved_webspace_id)
        if include_status_registry
        else None
    )
    payload = _thin_runtime_reliability_payload(
        status_registry,
        webspace_id=resolved_webspace_id,
        mode=mode,
    )
    return _json_response_with_etag(
        payload,
        if_none_match=if_none_match,
        mode=mode,
        started_at=started_at,
        endpoint=endpoint,
    )


def _runtime_beacon_unavailable_response(*, reason: str, timeout_s: float) -> Response:
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "available": False,
            "schema": "adaos.reliability_runtime_beacon.unavailable.v1",
            "reason": str(reason or "unavailable"),
            "retryable": True,
        },
        headers={
            "Cache-Control": "no-store",
            "Retry-After": "1",
            "X-AdaOS-Runtime-Executor": "dedicated",
            "X-AdaOS-Runtime-Stale": "unavailable",
            "X-AdaOS-Runtime-Fallback": str(reason or "unavailable"),
            "X-AdaOS-Runtime-Timeout-Ms": str(round(max(0.0, float(timeout_s)) * 1000.0, 3)),
        },
    )


def _compact_reliability_summary_payload(
    reliability: dict[str, Any],
    *,
    webspace_id: str | None,
    mode: str,
) -> dict[str, Any]:
    status_registry = _current_status_registry_snapshot(webspace_id=webspace_id)
    payload = _compact_runtime_reliability_payload(
        reliability,
        webspace_id=webspace_id,
        status_registry=status_registry,
    )
    payload["mode"] = mode
    return payload


def _webrtc_yjs_env_enabled() -> tuple[bool, str | None, str]:
    raw = os.getenv("ADAOS_WEBRTC_YJS_CHANNEL_ENABLED")
    if raw is None:
        return True, None, "default"
    normalized = str(raw).strip().lower()
    return normalized not in {"0", "false", "no", "off"}, str(raw), "env"


def _compact_webrtc_yjs_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    sync_runtime = _coerce_dict(runtime.get("sync_runtime"))
    if not sync_runtime and isinstance(runtime.get("transport"), dict):
        sync_runtime = runtime
    transport = _coerce_dict(sync_runtime.get("transport"))
    enabled, env_value, source = _webrtc_yjs_env_enabled()
    peer_total = int(transport.get("webrtc_peer_total") or 0)
    connected_peers = int(transport.get("webrtc_connected_peers") or 0)
    open_channels = int(transport.get("webrtc_open_yjs_channels") or 0)
    active_yws = int(transport.get("active_yws_connections") or 0)
    active_ws = int(transport.get("active_ws_connections") or 0)
    if not enabled:
        state = "disabled"
        reason = "env_disabled"
        blockers = ["ADAOS_WEBRTC_YJS_CHANNEL_ENABLED disables WebRTC Yjs DataChannel serving"]
    elif open_channels > 0:
        state = "ready"
        reason = "open_yjs_datachannel"
        blockers = []
    elif connected_peers > 0 or peer_total > 0:
        state = "warming"
        reason = "peer_without_open_yjs_channel"
        blockers = []
    elif active_yws > 0 or active_ws > 0:
        state = "standby"
        reason = "relay_active_no_yjs_datachannel"
        blockers = []
    else:
        state = "unknown"
        reason = "no_browser_transport_evidence"
        blockers = []
    return {
        "enabled": enabled,
        "state": state,
        "reason": reason,
        "source": source,
        "envVar": "ADAOS_WEBRTC_YJS_CHANNEL_ENABLED",
        "envValue": env_value,
        "peerTotal": peer_total,
        "connectedPeers": connected_peers,
        "openYjsChannels": open_channels,
        "activeYwsConnections": active_yws,
        "activeWsConnections": active_ws,
        "blockers": blockers,
    }


def _current_incident_registry_snapshot() -> dict[str, Any]:
    try:
        return incident_registry_snapshot(limit=50, include_evidence=False)
    except Exception:
        return {
            "schema": "adaos.incident_registry.v1",
            "available": False,
            "items": [],
            "active_total": 0,
        }


def _runtime_fault_from_incidents(incidents: dict[str, Any] | None) -> dict[str, Any]:
    registry = _coerce_dict(incidents)
    items = [_coerce_dict(item) for item in _coerce_list(registry.get("items"))]
    selected = [
        item
        for item in items
        if bool(item.get("active"))
        and (
            str(item.get("class") or "").strip() == "yjs_thread_affinity_fault"
            or str(item.get("signal") or "").strip() == "yjs_thread_affinity_fault"
        )
    ]
    if not selected:
        return {
            "state": "ready",
            "reason": None,
            "blockers": [],
            "incidents": [],
        }
    selected.sort(key=lambda item: float(item.get("last_seen_at") or 0.0), reverse=True)
    top = selected[0]
    severity = str(top.get("severity") or "degraded").strip().lower()
    state = "degraded" if severity in {"critical", "degraded", "high"} else "warning"
    blockers = sorted(
        {
            "yjs_thread_affinity_fault",
            *(
                str(item.get("signal") or "").strip()
                for item in selected
                if str(item.get("signal") or "").strip()
            ),
        }
    )
    return {
        "state": state,
        "reason": str(top.get("summary") or top.get("signal") or "yjs_thread_affinity_fault").strip()
        or "yjs_thread_affinity_fault",
        "blockers": blockers,
        "incidentId": str(top.get("id") or "").strip() or None,
        "severity": severity,
        "lastSeenAgoS": top.get("last_seen_ago_s"),
        "occurrenceCount": int(top.get("occurrence_count") or 0),
        "incidents": [
            {
                "id": str(item.get("id") or "").strip() or None,
                "severity": str(item.get("severity") or "").strip() or None,
                "summary": str(item.get("summary") or "").strip() or None,
                "lastSeenAgoS": item.get("last_seen_ago_s"),
            }
            for item in selected[:3]
        ],
    }


def _apply_runtime_fault_to_state_sync(
    state_sync: dict[str, Any],
    runtime_fault: dict[str, Any] | None,
) -> dict[str, Any]:
    fault = _coerce_dict(runtime_fault)
    if str(fault.get("state") or "").strip().lower() != "degraded":
        return state_sync
    updated = dict(state_sync)
    blockers = [
        str(item).strip()
        for item in _coerce_list(updated.get("blockers"))
        if str(item).strip()
    ]
    for item in _coerce_list(fault.get("blockers")) or ["yjs_thread_affinity_fault"]:
        token = str(item).strip()
        if token and token not in blockers:
            blockers.append(token)
    transport = str(updated.get("transportState") or "").strip().lower()
    if transport not in {"disconnected", "not_applicable"}:
        updated["transportState"] = "degraded"
    updated["semanticState"] = "degraded"
    updated["freshnessState"] = "stale"
    updated["fallbackMode"] = "hard_degraded_recovery"
    updated["blockers"] = blockers
    return updated


def _compact_state_sync_materialization(state_sync: dict[str, Any] | None) -> dict[str, Any]:
    materialization = _coerce_dict(_coerce_dict(state_sync).get("materialization"))
    if not materialization:
        return {}

    def _optional_text(key: str) -> str | None:
        value = str(materialization.get(key) or "").strip()
        return value or None

    return {
        "ready": bool(materialization.get("ready")),
        "readinessState": _optional_text("readiness_state"),
        "transitionExpected": bool(materialization.get("transition_expected")),
        "pending": bool(materialization.get("pending")),
        "status": _optional_text("status"),
        "currentScenario": _optional_text("current_scenario"),
        "targetScenario": _optional_text("target_scenario"),
        "missingBranches": [
            token
            for token in (str(item).strip() for item in _coerce_list(materialization.get("missing_branches")))
            if token
        ],
    }


def _compact_hub_browser_quality(
    *,
    connectivity: dict[str, Any],
    state_sync: dict[str, Any],
    webrtc_yjs: dict[str, Any],
    yjs_pressure: dict[str, Any] | None = None,
    eventbus_backlog: dict[str, Any] | None = None,
    runtime_fault: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def _text(payload: dict[str, Any], *keys: str, default: str = "unknown") -> str:
        for key in keys:
            raw = payload.get(key)
            if raw is not None:
                value = str(raw).strip()
                if value:
                    return value
        return default

    def _camel_or_snake(payload: dict[str, Any], camel: str, snake: str, default: str = "unknown") -> str:
        return _text(payload, camel, snake, default=default)

    def _gate(
        *,
        state: str,
        required: bool,
        reason: str | None = None,
        blockers: list[Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "state": state,
            "required": required,
            "reason": reason,
            "blockers": [str(item) for item in (blockers or []) if str(item).strip()],
            "evidence": evidence or {},
        }

    def _route_state(route: dict[str, Any]) -> str:
        transport = _camel_or_snake(route, "transportState", "transport_state").strip().lower()
        transition = _camel_or_snake(route, "transitionState", "transition_state").strip().lower()
        blockers = _coerce_list(route.get("blockers"))
        if blockers or transport in {"degraded", "failed", "offline", "down", "error"}:
            return "degraded"
        if transition in {"reconnecting", "link_starting", "starting", "recovering", "waiting_restart"}:
            return "recovering"
        if transport in {"ready", "attached", "connected", "online", "ok", "healthy"}:
            return "ready"
        return "unknown"

    def _sync_state(sync: dict[str, Any]) -> str:
        transport = _camel_or_snake(sync, "transportState", "transport_state").strip().lower()
        first_sync = _camel_or_snake(sync, "firstSyncState", "first_sync_state").strip().lower()
        semantic = _camel_or_snake(sync, "semanticState", "semantic_state").strip().lower()
        freshness = _camel_or_snake(sync, "freshnessState", "freshness_state").strip().lower()
        blockers = [
            item
            for item in _coerce_list(sync.get("blockers"))
            if str(item).strip() not in {"bounded_sync_runtime_observed"}
        ]
        if blockers or transport in {"degraded", "failed", "offline", "down", "error"}:
            return "degraded"
        if first_sync in {"timeout", "failed", "blocked"} or semantic in {"degraded", "blocked", "failed"}:
            return "degraded"
        if (
            transport in {"attached", "ready", "connected", "online", "ok", "healthy"}
            and first_sync in {"complete", "ready", "done"}
            and semantic in {"ready", "ok", "healthy"}
            and freshness in {"fresh", "current", "ready", "ok", "healthy"}
        ):
            return "ready"
        if transport in {"attached", "ready", "connected"} or first_sync in {"starting", "pending", "running"}:
            return "recovering"
        return "unknown"

    def _pressure_state(pressure: dict[str, Any]) -> str:
        if not pressure:
            return "unknown"
        policy = _camel_or_snake(pressure, "policyState", "policy_state", default="ok").strip().lower()
        observed = _camel_or_snake(pressure, "observedState", "observed_state", default="idle").strip().lower()
        if policy in {"high", "critical", "blocked", "degraded"}:
            return "degraded"
        if policy in {"warn", "warning", "throttle", "throttled"} or observed in {"high", "critical"}:
            return "warning"
        return "ready"

    def _eventbus_state(backlog: dict[str, Any]) -> str:
        if not backlog:
            return "unknown"
        pending = int(backlog.get("pendingTasks") or backlog.get("pending_tasks") or 0)
        bounded_total = int(backlog.get("boundedQueueTotal") or backlog.get("bounded_queue_total") or 0)
        bounded_peak = int(backlog.get("boundedQueuePeak") or backlog.get("bounded_queue_peak") or 0)
        if bounded_total > 0 or pending > 0:
            return "warning"
        if bounded_peak > 0:
            return "ready"
        return "ready"

    browser_route = _coerce_dict(
        connectivity.get("browserControlRoute") or connectivity.get("browser_control_route")
    )
    required_link = _coerce_dict(
        connectivity.get("requiredUpstreamLink") or connectivity.get("required_upstream_link")
    )
    pressure = _coerce_dict(yjs_pressure)
    backlog = _coerce_dict(eventbus_backlog)
    fault = _coerce_dict(runtime_fault)
    route_state = _route_state(browser_route)
    sync_state = _sync_state(state_sync)
    pressure_state = _pressure_state(pressure)
    eventbus_state = _eventbus_state(backlog)
    fault_state = str(fault.get("state") or "ready").strip().lower() or "ready"
    webrtc_state = _text(webrtc_yjs, "state", default="unknown").strip().lower()
    webrtc_enabled = bool(webrtc_yjs.get("enabled"))
    webrtc_gate_state = "ready" if webrtc_state == "ready" else "fallback"
    if webrtc_state in {"unknown", ""}:
        webrtc_gate_state = "unknown"

    gates = {
        "browserControlRoute": _gate(
            state=route_state,
            required=True,
            reason=_camel_or_snake(browser_route, "reason", "reason", default="") or None,
            blockers=_coerce_list(browser_route.get("blockers")),
            evidence={
                "transportState": _camel_or_snake(browser_route, "transportState", "transport_state"),
                "transitionState": _camel_or_snake(browser_route, "transitionState", "transition_state"),
                "servedBy": _camel_or_snake(browser_route, "servedBy", "served_by", default="") or None,
            },
        ),
        "stateSync": _gate(
            state=sync_state,
            required=True,
            reason=_camel_or_snake(state_sync, "fallbackMode", "fallback_mode", default="off"),
            blockers=_coerce_list(state_sync.get("blockers")),
            evidence={
                "transportState": _camel_or_snake(state_sync, "transportState", "transport_state"),
                "firstSyncState": _camel_or_snake(state_sync, "firstSyncState", "first_sync_state"),
                "semanticState": _camel_or_snake(state_sync, "semanticState", "semantic_state"),
                "freshnessState": _camel_or_snake(state_sync, "freshnessState", "freshness_state"),
            },
        ),
        "webrtcYjsUpgrade": _gate(
            state=webrtc_gate_state,
            required=False,
            reason=_text(webrtc_yjs, "reason", default="") or None,
            blockers=_coerce_list(webrtc_yjs.get("blockers")),
            evidence={
                "enabled": webrtc_enabled,
                "state": webrtc_state or "unknown",
                "peerTotal": int(webrtc_yjs.get("peerTotal") or webrtc_yjs.get("peer_total") or 0),
                "openYjsChannels": int(webrtc_yjs.get("openYjsChannels") or webrtc_yjs.get("open_yjs_channels") or 0),
                "activeYwsConnections": int(
                    webrtc_yjs.get("activeYwsConnections") or webrtc_yjs.get("active_yws_connections") or 0
                ),
            },
        ),
        "yjsPressure": _gate(
            state=pressure_state,
            required=False,
            reason=_camel_or_snake(pressure, "reason", "reason", default="") or None,
            blockers=_coerce_list(pressure.get("blockedRoots") or pressure.get("blocked_roots")),
            evidence={
                "policyState": _camel_or_snake(pressure, "policyState", "policy_state", default="ok"),
                "observedState": _camel_or_snake(pressure, "observedState", "observed_state", default="idle"),
                "recentBytes": int(pressure.get("recentBytes") or pressure.get("recent_bytes") or 0),
                "recentWrites": int(pressure.get("recentWrites") or pressure.get("recent_writes") or 0),
            },
        ),
        "eventbusBacklog": _gate(
            state=eventbus_state,
            required=False,
            reason="bounded_queue_or_pending_tasks" if eventbus_state == "warning" else None,
            evidence={
                "pendingTasks": int(backlog.get("pendingTasks") or backlog.get("pending_tasks") or 0),
                "boundedQueueTotal": int(backlog.get("boundedQueueTotal") or backlog.get("bounded_queue_total") or 0),
                "boundedQueuePeak": int(backlog.get("boundedQueuePeak") or backlog.get("bounded_queue_peak") or 0),
            },
        ),
        "runtimeFault": _gate(
            state=fault_state if fault_state in {"ready", "warning", "degraded"} else "unknown",
            required=False,
            reason=str(fault.get("reason") or "").strip() or None,
            blockers=_coerce_list(fault.get("blockers")),
            evidence={
                "incidentId": str(fault.get("incidentId") or "").strip() or None,
                "severity": str(fault.get("severity") or "").strip() or None,
                "lastSeenAgoS": fault.get("lastSeenAgoS"),
                "occurrenceCount": int(fault.get("occurrenceCount") or 0),
            },
        ),
    }

    required_states = [gates["browserControlRoute"]["state"], gates["stateSync"]["state"]]
    if "degraded" in required_states:
        logical_state = "degraded"
    elif "recovering" in required_states:
        logical_state = "recovering"
    elif all(state == "ready" for state in required_states):
        logical_state = "ready"
    else:
        logical_state = "unknown"

    all_states = [str(gate.get("state") or "unknown") for gate in gates.values()]
    if logical_state == "degraded" or "degraded" in all_states:
        quality_state = "degraded"
    elif logical_state == "recovering" or "recovering" in all_states:
        quality_state = "recovering"
    elif "warning" in all_states:
        quality_state = "warning"
    elif "fallback" in all_states:
        quality_state = "fallback"
    elif logical_state == "ready":
        quality_state = "ready"
    else:
        quality_state = "unknown"

    fallbacks: list[dict[str, Any]] = []
    if webrtc_gate_state == "fallback":
        fallbacks.append(
            {
                "channel": "sync",
                "from": "webrtc_data:yjs",
                "to": "yws",
                "reason": _text(webrtc_yjs, "reason", default="webrtc_yjs_not_ready"),
            }
        )
    if route_state != "ready":
        fallbacks.append(
            {
                "channel": "command_event_presence",
                "from": "preferred_route",
                "to": "available_ws_or_http",
                "reason": _camel_or_snake(browser_route, "reason", "reason", default="browser_route_not_ready"),
            }
        )

    blockers = sorted(
        {
            str(item).strip()
            for gate in gates.values()
            for item in _coerce_list(gate.get("blockers"))
            if str(item).strip() and str(item).strip() not in {"bounded_sync_runtime_observed"}
        }
    )
    warnings = [
        gate_id
        for gate_id, gate in gates.items()
        if str(gate.get("state") or "") in {"warning", "fallback", "recovering"}
    ]
    route_kind = _camel_or_snake(required_link, "kind", "kind", default="")
    route_transport = "root_routed_ws" if route_kind == "hub_root" else "local_or_runtime_ws"
    return {
        "schema": "adaos.hub_browser_quality.v1",
        "logicalState": logical_state,
        "qualityState": quality_state,
        "summary": f"logical={logical_state} quality={quality_state}",
        "activeTransports": {
            "route": route_transport,
            "control": "ws" if route_state in {"ready", "recovering"} else "ws_unhealthy",
            "sync": "yws" if sync_state in {"ready", "recovering"} else "yws_unhealthy",
            "yjsDirect": "webrtc_data:yjs" if webrtc_state == "ready" else None,
            "yjsFallback": "yws" if webrtc_state != "ready" else None,
        },
        "gates": gates,
        "fallbacks": fallbacks,
        "blockers": blockers,
        "warnings": warnings,
    }


def _compact_member_availability(value: Any) -> dict[str, Any]:
    payload = _coerce_dict(value)
    role = str(payload.get("role") or "unknown").strip() or "unknown"
    assessment = _coerce_dict(payload.get("assessment"))
    result = {
        "source": "hub_member_connection_state" if payload else "unavailable",
        "role": role,
        "state": str(assessment.get("state") or "unknown").strip() or "unknown",
        "reason": str(assessment.get("reason") or "").strip() or None,
        "total": 0,
        "online": 0,
        "stale": 0,
        "offline": 0,
        "updating": 0,
        "unknown": 0,
        "excluded": 0,
        "dormant": 0,
        "connectedTotal": 0,
        "knownTotal": 0,
        "linklessTotal": 0,
        "mediaCapableReady": 0,
        "mediaCapableTotal": 0,
        "directCandidatesReady": 0,
        "directCandidatesTotal": 0,
        "blockingMembers": [],
    }
    if not payload:
        return result

    if role == "hub":
        known_members = _coerce_list(payload.get("known_members"))
        connected_members = _coerce_list(payload.get("members"))
        members = known_members or connected_members
        result["connectedTotal"] = int(payload.get("connected_total") or len(connected_members) or 0)
        result["knownTotal"] = int(payload.get("known_total") or len(members) or result["connectedTotal"])
        result["linklessTotal"] = int(payload.get("linkless_total") or 0)
        active_total = 0
        for item in members:
            if not isinstance(item, dict):
                result["unknown"] += 1
                active_total += 1
                continue
            managed_state = str(item.get("managed_state") or item.get("policy_state") or "").strip().lower()
            revoked = bool(item.get("revoked"))
            expired = bool(item.get("expired"))
            if revoked or expired or managed_state in {"revoked", "expired", "disabled", "ignored", "retired", "deleted"}:
                result["excluded"] += 1
                continue
            if str(item.get("availability_scope") or "").strip().lower() == "dormant":
                result["dormant"] += 1
                continue
            active_total += 1
            connected = bool(item.get("connected"))
            online = bool(item.get("online"))
            snapshot_state = str(item.get("snapshot_state") or "").strip().lower()
            rollout_state = str(item.get("rollout_state") or "").strip().lower()
            label = str(item.get("label") or item.get("node_label") or item.get("node_id") or "").strip()
            node_id = str(item.get("node_id") or "").strip()
            media_capable = bool(item.get("media_capable"))
            if media_capable:
                result["mediaCapableReady"] += 1
                result["directCandidatesReady"] += 1
            if item.get("media_capability") is not None or media_capable:
                result["mediaCapableTotal"] += 1
                result["directCandidatesTotal"] += 1
            if rollout_state in {"in_progress", "transitioning"}:
                state = "updating"
            elif rollout_state in {"failed"}:
                state = "offline"
            elif connected:
                state = "online"
            elif online or snapshot_state in {"stale", "pending"}:
                state = "stale"
            elif not node_id and not label:
                state = "unknown"
            else:
                state = "offline"
            result[state] += 1
            if state in {"stale", "offline", "updating"} and len(result["blockingMembers"]) < 8:
                result["blockingMembers"].append(
                    {
                        "nodeId": node_id or None,
                        "label": label or node_id or "member",
                        "state": state,
                        "reason": rollout_state or snapshot_state or ("link_missing" if not connected else None),
                    }
                )
        result["total"] = max(active_total, result["connectedTotal"] - result["excluded"], 0)
        return result

    connected = bool(payload.get("connected_to_hub")) or bool(payload.get("connected_to_subnet"))
    state = str(payload.get("state") or "").strip().lower()
    result["total"] = 1
    result["connectedTotal"] = 1 if connected else 0
    result["knownTotal"] = 1
    if connected or state == "connected":
        result["online"] = 1
    elif state in {"waiting_restart", "restarting", "paused_for_update"}:
        result["updating"] = 1
    elif state:
        result["offline"] = 1
    else:
        result["unknown"] = 1
    if result["offline"] or result["updating"]:
        local_node = _coerce_dict(payload.get("local_node"))
        result["blockingMembers"].append(
            {
                "nodeId": str(local_node.get("node_id") or "").strip() or None,
                "label": str(local_node.get("label") or local_node.get("node_label") or "local member").strip(),
                "state": "updating" if result["updating"] else "offline",
                "reason": state or result["reason"],
            }
        )
    return result


def _compact_phase0_task(value: Any) -> dict[str, Any] | None:
    payload = _coerce_dict(value)
    if not payload:
        return None
    return {
        "id": str(payload.get("id") or "").strip(),
        "status": str(payload.get("status") or "unknown").strip() or "unknown",
        "summary": str(payload.get("summary") or "").strip(),
        "completedCriteria": _coerce_list(payload.get("completed_criteria")),
        "pendingCriteria": _coerce_list(payload.get("pending_criteria")),
        "pendingReasons": _coerce_list(payload.get("pending_reasons")),
        "evidence": _coerce_dict(payload.get("evidence")),
    }


def _compact_phase0_checkpoint(value: Any) -> dict[str, Any] | None:
    payload = _coerce_dict(value)
    if not payload:
        return None
    tasks = _coerce_dict(payload.get("tasks"))
    return {
        "state": str(payload.get("state") or "unknown").strip() or "unknown",
        "ready": bool(payload.get("ready")),
        "trackedTasks": _coerce_list(payload.get("tracked_tasks")),
        "completedTaskTotal": int(payload.get("completed_task_total") or 0),
        "taskTotal": int(payload.get("task_total") or 0),
        "remainingTasks": _coerce_list(payload.get("remaining_tasks")),
        "tasks": {
            "nodeBrowserReady": _compact_phase0_task(tasks.get("phase0.node_browser_ready")),
            "runtimeCommReady": _compact_phase0_task(tasks.get("phase0.runtime_comm_ready")),
        },
    }


def _compact_route_tunnel_state(value: Any) -> str:
    payload = _coerce_dict(value)
    current_owner = str(payload.get("current_owner") or "").strip().lower()
    planned_owner = str(payload.get("planned_owner") or "").strip().lower()
    current_support = str(payload.get("current_support") or "").strip().lower()
    delegation_mode = str(payload.get("delegation_mode") or "").strip().lower()
    listener_ready = bool(payload.get("listener_ready"))
    handoff_ready = bool(payload.get("handoff_ready"))
    if current_owner == "sidecar":
        if handoff_ready:
            return "ready"
        if listener_ready:
            return "starting"
        return "degraded"
    if planned_owner == "sidecar":
        if listener_ready or current_support == "proxy_ready" or delegation_mode in {"local_tcp_proxy", "local_ws_proxy"}:
            return "proxy_ready" if listener_ready or current_support == "proxy_ready" else "planned"
        return "disabled" if current_support == "disabled" else "planned"
    if current_owner == "runtime":
        if listener_ready or current_support == "proxy_ready" or delegation_mode in {"local_tcp_proxy", "local_ws_proxy"}:
            return "proxy_ready" if listener_ready or current_support == "proxy_ready" else "not_owned"
        return "not_owned"
    return "unknown"


def _compact_sidecar_runtime_fields(sidecar_runtime: dict[str, Any]) -> dict[str, Any]:
    sidecar = _coerce_dict(sidecar_runtime)
    sidecar_enablement = _coerce_dict(sidecar.get("enablement"))
    continuity = _coerce_dict(sidecar.get("continuity_contract"))
    progress = _coerce_dict(sidecar.get("progress"))
    route_tunnel = _coerce_dict(sidecar.get("route_tunnel_contract"))
    ws = _coerce_dict(route_tunnel.get("ws"))
    yws = _coerce_dict(route_tunnel.get("yws"))
    return {
        "sidecarContinuity": {
            "currentSupport": str(continuity.get("current_support") or "unknown").strip() or "unknown",
            "hubRuntimeUpdate": str(continuity.get("hub_runtime_update") or "unknown").strip() or "unknown",
            "required": bool(continuity.get("required")),
            "pendingBoundaries": _coerce_list(continuity.get("pending_boundaries")),
            "readyBoundaries": _coerce_list(continuity.get("ready_boundaries")),
            "blockers": _coerce_list(continuity.get("blockers")),
        },
        "sidecarEnablement": {
            "enabled": bool(sidecar_enablement.get("enabled")),
            "defaultEnabled": bool(sidecar_enablement.get("default_enabled")),
            "explicit": bool(sidecar_enablement.get("explicit")),
            "source": str(sidecar_enablement.get("source") or "unknown").strip() or "unknown",
            "role": str(sidecar_enablement.get("role") or "").strip() or None,
            "envVar": str(sidecar_enablement.get("env_var") or "").strip() or None,
            "envValue": str(sidecar_enablement.get("env_value") or "").strip() or None,
            "reason": str(sidecar_enablement.get("reason") or "").strip() or None,
        },
        "sidecarProgress": {
            "state": str(progress.get("state") or "unknown").strip() or "unknown",
            "percent": float(progress.get("percent") or 0),
            "completedMilestones": int(progress.get("completed_milestones") or 0),
            "milestoneTotal": int(progress.get("milestone_total") or 0),
            "currentMilestone": str(progress.get("current_milestone") or "").strip() or None,
            "nextBlocker": str(progress.get("next_blocker") or "").strip() or None,
        },
        "sidecarTransportReady": bool(sidecar.get("transport_ready")),
        "sidecarRemoteSessionState": str(sidecar.get("remote_session_state") or "unknown").strip() or "unknown",
        "sidecarSessionState": str(sidecar.get("session_state") or "unknown").strip() or "unknown",
        "sidecarStatusReason": str(sidecar.get("status_reason") or "").strip() or None,
        "routeTunnel": {
            "currentSupport": str(route_tunnel.get("current_support") or "unknown").strip() or "unknown",
            "ownershipBoundary": str(route_tunnel.get("ownership_boundary") or "unknown").strip() or "unknown",
            "ws": ws,
            "yws": yws,
        },
        "browserWsHandoffReady": str(ws.get("current_owner") or "").strip().lower() == "sidecar" and bool(ws.get("handoff_ready")),
        "browserYwsHandoffReady": str(yws.get("current_owner") or "").strip().lower() == "sidecar" and bool(yws.get("handoff_ready")),
        "browserWsHandoffState": _compact_route_tunnel_state(ws),
        "browserYwsHandoffState": _compact_route_tunnel_state(yws),
        "browserWsHandoffBlocker": (str((_coerce_list(ws.get("blockers"))[:1] or [""])[0]).strip() or None),
        "browserYwsHandoffBlocker": (str((_coerce_list(yws.get("blockers"))[:1] or [""])[0]).strip() or None),
    }


def _thin_sidecar_runtime_fields() -> dict[str, Any]:
    try:
        from adaos.services.reliability import sidecar_runtime_snapshot

        sidecar = sidecar_runtime_snapshot(
            role=None,
            readiness_tree={},
            hub_root_protocol={},
            transport_strategy={},
            media_runtime={},
        )
    except Exception as exc:
        sidecar = {
            "enablement": {
                "enabled": False,
                "default_enabled": False,
                "explicit": False,
                "source": "unavailable",
                "reason": f"{type(exc).__name__}: {exc}",
            },
            "continuity_contract": {
                "current_support": "unknown",
                "hub_runtime_update": "unknown",
                "required": False,
                "pending_boundaries": [],
                "ready_boundaries": [],
                "blockers": [],
            },
            "progress": {},
            "route_tunnel_contract": {},
        }
    return _compact_sidecar_runtime_fields(sidecar if isinstance(sidecar, dict) else {})


def _compact_runtime_reliability_payload(
    payload: dict[str, Any],
    *,
    webspace_id: str | None = None,
    status_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = _coerce_dict(payload.get("runtime"))
    hub_root_protocol = _coerce_dict(runtime.get("hub_root_protocol"))
    sidecar_runtime = _coerce_dict(runtime.get("sidecar_runtime"))
    sidecar_fields = _compact_sidecar_runtime_fields(sidecar_runtime)
    hardening = _coerce_dict(hub_root_protocol.get("hardening_coverage"))
    supervisor_runtime = _coerce_dict(runtime.get("supervisor_runtime"))
    connectivity = _coerce_dict(runtime.get("connectivity"))
    required_upstream_link = _coerce_dict(connectivity.get("required_upstream_link"))
    browser_control_route = _coerce_dict(connectivity.get("browser_control_route"))
    state_sync = _coerce_dict(runtime.get("state_sync"))
    replay = _coerce_dict(state_sync.get("replay"))
    hub_member_connection_state = _coerce_dict(runtime.get("hub_member_connection_state"))
    yjs_pressure = _coerce_dict(runtime.get("yjs_pressure"))
    yjs_projection_guard = _coerce_dict(runtime.get("yjs_projection_guard"))
    yjs_projection_guard_totals = _coerce_dict(yjs_projection_guard.get("totals"))
    yjs_projection_guard_items = _coerce_list(yjs_projection_guard.get("items"))
    yjs_projection_guard_top = _coerce_dict(
        yjs_projection_guard_items[0] if yjs_projection_guard_items else {}
    )
    webio_stream_guard = _coerce_dict(runtime.get("webio_stream_guard"))
    webio_stream_guard_totals = _coerce_dict(webio_stream_guard.get("totals"))
    webio_stream_guard_items = _coerce_list(webio_stream_guard.get("items"))
    webio_stream_guard_top = _coerce_dict(
        webio_stream_guard_items[0] if webio_stream_guard_items else {}
    )
    eventbus_backlog = _coerce_dict(runtime.get("eventbus_backlog"))
    webio_control_items = _coerce_list(eventbus_backlog.get("top_webio_stream_controls"))
    compact_webrtc_yjs = _compact_webrtc_yjs_runtime(runtime)
    incidents = _coerce_dict(runtime.get("incident_registry"))
    if not incidents:
        incidents = _current_incident_registry_snapshot()
    runtime_fault = _runtime_fault_from_incidents(incidents)
    runtime_for_cards = dict(runtime)
    if str(runtime_fault.get("state") or "").strip().lower() == "degraded":
        runtime_for_cards["incident_registry"] = incidents
    resolved_webspace_id = _coerce_node_webspace_id(
        webspace_id
        or runtime.get("webspace_id")
        or payload.get("webspace_id")
    )
    status_snapshot = _with_derived_status_cards(
        status_registry or _current_status_registry_snapshot(webspace_id=resolved_webspace_id),
        guard_status_cards_from_runtime(runtime_for_cards, webspace_id=resolved_webspace_id),
    )
    compact_state_sync = {
        "webspaceId": str(state_sync.get("webspace_id") or resolved_webspace_id).strip() or resolved_webspace_id,
        "transportState": str(state_sync.get("transport_state") or "unknown").strip() or "unknown",
        "firstSyncState": str(state_sync.get("first_sync_state") or "unknown").strip() or "unknown",
        "semanticState": str(state_sync.get("semantic_state") or "unknown").strip() or "unknown",
        "freshnessState": str(state_sync.get("freshness_state") or "unknown").strip() or "unknown",
        "lastGoodSyncAt": state_sync.get("last_good_sync_at"),
        "lastMaterializationAt": state_sync.get("last_materialization_at"),
        "replay": {
            "mode": str(replay.get("mode") or "snapshot_plus_diff").strip() or "snapshot_plus_diff",
            "cursor": str(replay.get("cursor") or "0/0").strip() or "0/0",
        },
        "fallbackMode": str(state_sync.get("fallback_mode") or "off").strip() or "off",
        "blockers": _coerce_list(state_sync.get("blockers")),
    }
    compact_materialization = _compact_state_sync_materialization(state_sync)
    if compact_materialization:
        compact_state_sync["materialization"] = compact_materialization
    compact_state_sync = _apply_runtime_fault_to_state_sync(compact_state_sync, runtime_fault)
    compact_connectivity = {
        "requiredUpstreamLink": {
            "kind": str(required_upstream_link.get("kind") or "").strip() or None,
            "scopeId": str(required_upstream_link.get("scope_id") or "").strip() or None,
            "transportState": str(required_upstream_link.get("transport_state") or "unknown").strip() or "unknown",
            "transitionState": str(required_upstream_link.get("transition_state") or "unknown").strip() or "unknown",
            "plannedTransition": _coerce_dict(required_upstream_link.get("planned_transition")),
            "reason": str(required_upstream_link.get("reason") or "").strip() or None,
            "blockers": _coerce_list(required_upstream_link.get("blockers")),
            "servedBy": str(required_upstream_link.get("served_by") or "").strip() or None,
        },
        "browserControlRoute": {
            "kind": str(browser_control_route.get("kind") or "").strip() or "browser_control_route",
            "scopeId": str(browser_control_route.get("scope_id") or "").strip() or None,
            "transportState": str(browser_control_route.get("transport_state") or "unknown").strip() or "unknown",
            "transitionState": str(browser_control_route.get("transition_state") or "unknown").strip() or "unknown",
            "plannedTransition": _coerce_dict(browser_control_route.get("planned_transition")),
            "reason": str(browser_control_route.get("reason") or "").strip() or None,
            "blockers": _coerce_list(browser_control_route.get("blockers")),
            "servedBy": str(browser_control_route.get("served_by") or "").strip() or None,
        },
    }
    return {
        "ok": True,
        "updatedAt": int(time.time() * 1000),
        "available": True,
        "source": "api.node.reliability.summary",
        "webspaceId": resolved_webspace_id,
        "hubRootHardening": {
            "state": str(hardening.get("state") or "unknown").strip() or "unknown",
            "coveredFlows": int(hardening.get("covered_flows") or 0),
            "totalFlows": int(hardening.get("total_flows") or 0),
            "flows": _coerce_list(hardening.get("flows")),
        },
        **sidecar_fields,
        "connectivity": compact_connectivity,
        "stateSync": compact_state_sync,
        "runtimeFault": runtime_fault,
        "memberAvailability": _compact_member_availability(hub_member_connection_state),
        "webrtcYjs": compact_webrtc_yjs,
        "yjsPressure": {
            "webspaceId": str(yjs_pressure.get("webspace_id") or resolved_webspace_id).strip() or resolved_webspace_id,
            "owner": str(yjs_pressure.get("owner") or "").strip() or None,
            "recentBytes": int(yjs_pressure.get("recent_bytes") or 0),
            "recentWrites": int(yjs_pressure.get("recent_writes") or 0),
            "peakBps": float(yjs_pressure.get("peak_bps") or 0.0),
            "peakWps": float(yjs_pressure.get("peak_wps") or 0.0),
            "policyState": str(yjs_pressure.get("policy_state") or "ok").strip() or "ok",
            "target": str(yjs_pressure.get("target") or "primary_shared_doc").strip() or "primary_shared_doc",
            "reason": str(yjs_pressure.get("reason") or "").strip() or None,
            "blockedRoots": _coerce_list(yjs_pressure.get("blocked_roots")),
            "observedState": str(yjs_pressure.get("observed_state") or "idle").strip() or "idle",
            "lastRoute": _coerce_dict(yjs_pressure.get("last_route")),
            "lastProjection": _coerce_dict(yjs_pressure.get("last_projection")),
        },
        "yjsProjectionGuard": {
            "available": bool(yjs_projection_guard.get("available")),
            "enabled": bool(yjs_projection_guard.get("enabled")),
            "webspaceId": str(yjs_projection_guard.get("webspace_id") or resolved_webspace_id).strip()
            or resolved_webspace_id,
            "total": int(yjs_projection_guard.get("total") or 0),
            "totals": {
                "guarded": int(yjs_projection_guard_totals.get("guarded") or 0),
            },
            "top": {
                "owner": str(yjs_projection_guard_top.get("owner") or "").strip() or None,
                "scope": str(yjs_projection_guard_top.get("scope") or "").strip() or None,
                "slot": str(yjs_projection_guard_top.get("slot") or "").strip() or None,
                "path": str(yjs_projection_guard_top.get("path") or "").strip() or None,
                "root": str(yjs_projection_guard_top.get("root") or "").strip() or None,
                "reason": str(yjs_projection_guard_top.get("reason") or "").strip() or None,
                "payloadBytes": int(yjs_projection_guard_top.get("payload_bytes") or 0),
                "updateBytes": int(yjs_projection_guard_top.get("update_bytes") or 0),
                "amplificationRatio": float(yjs_projection_guard_top.get("amplification_ratio") or 0.0),
                "degradedBytes": int(yjs_projection_guard_top.get("degraded_bytes") or 0),
                "maxPayloadBytes": _coerce_optional_int(yjs_projection_guard_top.get("max_payload_bytes")),
                "maxItems": _coerce_optional_int(yjs_projection_guard_top.get("max_items")),
                "maxListItems": int(yjs_projection_guard_top.get("max_list_items") or 0),
                "maxListPath": str(yjs_projection_guard_top.get("max_list_path") or "").strip() or None,
                "listItemTotal": int(yjs_projection_guard_top.get("list_item_total") or 0),
                "guarded": int(yjs_projection_guard_top.get("guarded_total") or 0),
                "lastAt": yjs_projection_guard_top.get("last_at"),
            },
        },
        "hubBrowserQuality": _compact_hub_browser_quality(
            connectivity=compact_connectivity,
            state_sync=compact_state_sync,
            webrtc_yjs=compact_webrtc_yjs,
            yjs_pressure=yjs_pressure,
            eventbus_backlog=eventbus_backlog,
            runtime_fault=runtime_fault,
        ),
        "webioStreamGuard": {
            "available": bool(webio_stream_guard.get("available")),
            "webspaceId": str(webio_stream_guard.get("webspace_id") or resolved_webspace_id).strip() or resolved_webspace_id,
            "total": int(webio_stream_guard.get("total") or 0),
            "totals": {
                "attempted": int(webio_stream_guard_totals.get("attempted") or 0),
                "published": int(webio_stream_guard_totals.get("published") or 0),
                "suppressed": int(webio_stream_guard_totals.get("suppressed") or 0),
                "throttled": int(webio_stream_guard_totals.get("throttled") or 0),
                "publishedFanout": int(webio_stream_guard_totals.get("published_fanout") or 0),
            },
            "top": {
                "receiver": str(webio_stream_guard_top.get("receiver") or "").strip() or None,
                "owner": str(webio_stream_guard_top.get("owner") or "").strip() or None,
                "surface": str(webio_stream_guard_top.get("surface") or "").strip() or None,
                "attempted": int(webio_stream_guard_top.get("attempted_total") or 0),
                "published": int(webio_stream_guard_top.get("published_total") or 0),
                "suppressed": int(webio_stream_guard_top.get("suppressed_total") or 0),
                "throttled": int(webio_stream_guard_top.get("throttled_total") or 0),
                "declaredMaxPayloadBytes": _coerce_optional_int(
                    webio_stream_guard_top.get("declared_max_payload_bytes")
                ),
                "lastReason": str(webio_stream_guard_top.get("last_reason") or "").strip() or None,
            },
        },
        "eventbusBacklog": {
            "available": bool(eventbus_backlog.get("available")),
            "pendingTasks": int(eventbus_backlog.get("pending_tasks") or 0),
            "pendingPeak": int(eventbus_backlog.get("pending_peak") or 0),
            "boundedQueueTotal": int(eventbus_backlog.get("bounded_queue_total") or 0),
            "boundedQueuePeak": int(eventbus_backlog.get("bounded_queue_peak") or 0),
            "boundedActiveWorkers": int(eventbus_backlog.get("bounded_active_workers") or 0),
            "topWebioStreamControls": [
                {
                    "eventType": str(item.get("event_type") or "").strip() or None,
                    "webspaceId": str(item.get("webspace_id") or resolved_webspace_id).strip() or resolved_webspace_id,
                    "targetNodeId": str(item.get("target_node_id") or "").strip() or None,
                    "receiver": str(item.get("receiver") or "").strip() or None,
                    "source": str(item.get("source") or "").strip() or None,
                    "incoming": int(item.get("incoming_total") or 0),
                    "queued": int(item.get("queued_total") or 0),
                    "superseded": int(item.get("superseded_total") or 0),
                    "dropped": int(item.get("dropped_total") or 0),
                    "lastAction": str(item.get("last_action") or "").strip() or None,
                }
                for item in webio_control_items[:5]
                if isinstance(item, dict)
            ],
        },
        "supervisorRuntime": supervisor_runtime,
        "phase0Communication": _compact_phase0_checkpoint(runtime.get("event_model_phase0_communication")),
        "statusPlane": _compact_status_registry_payload(
            status_snapshot,
            webspace_id=resolved_webspace_id,
            limit=20,
            source="api.node.reliability.summary.status_plane",
        ),
    }


def _env_flag_enabled(name: str) -> bool:
    return env_bool(name)


def _supervisor_enabled() -> bool:
    return env_bool("ADAOS_SUPERVISOR_ENABLED")


def _supervisor_base_url() -> str | None:
    raw = str(os.getenv("ADAOS_SUPERVISOR_URL") or "").strip()
    if raw:
        return raw.rstrip("/")
    return supervisor_base_from_env()


async def _proxy_supervisor_json(
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    if not _supervisor_enabled():
        raise HTTPException(status_code=503, detail="supervisor-backed control surface is unavailable")
    base_url = _supervisor_base_url()
    if not base_url:
        raise HTTPException(status_code=503, detail="supervisor control URL is unavailable")

    headers = {"Accept": "application/json"}
    token = str(os.getenv("ADAOS_TOKEN") or "").strip()
    if token:
        headers["X-AdaOS-Token"] = token
    if payload is not None:
        headers["Content-Type"] = "application/json"
    url = f"{base_url}{path}"

    def _send() -> dict[str, Any]:
        session = requests.Session()
        try:
            try:
                session.trust_env = False
            except Exception:
                pass
            response = session.request(
                str(method or "GET").upper(),
                url,
                headers=headers,
                json=payload,
                timeout=float(timeout),
            )
            if int(response.status_code or 0) >= 400:
                try:
                    detail: Any = response.json()
                except Exception:
                    detail = (response.text or f"supervisor returned HTTP {response.status_code}").strip()[:500]
                if isinstance(detail, dict) and set(detail.keys()) == {"detail"}:
                    detail = detail["detail"]
                raise HTTPException(status_code=int(response.status_code), detail=detail)
            body = response.json()
            if not isinstance(body, dict):
                raise RuntimeError("supervisor returned a non-object payload")
            return body
        finally:
            try:
                session.close()
            except Exception:
                pass

    try:
        return await anyio.to_thread.run_sync(_send)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"supervisor API unavailable: {type(exc).__name__}: {exc}") from exc


def _publish_yjs_control_event(
    *,
    action: str,
    webspace_id: str,
    result: dict[str, Any],
    scenario_id: str | None = None,
) -> None:
    payload = {
        "action": str(action or "").strip(),
        "webspace_id": _coerce_node_webspace_id(webspace_id),
        "scenario_id": str(scenario_id or result.get("scenario_id") or "").strip() or None,
        "ok": bool(result.get("ok")),
        "accepted": bool(result.get("accepted")),
        "source_of_truth": str(result.get("source_of_truth") or "").strip() or None,
        "home_scenario": str(result.get("home_scenario") or "").strip() or None,
        "background_rebuild": bool(result.get("background_rebuild")),
        "switch_skipped": bool(result.get("switch_skipped")),
        "skip_reason": str(result.get("skip_reason") or "").strip() or None,
        "error": str(result.get("error") or "").strip() or None,
    }
    event_type = "node.yjs.control.completed" if payload["ok"] and payload["accepted"] else "node.yjs.control.failed"
    try:
        get_ctx().bus.publish(
            Event(
                type=event_type,
                payload=payload,
                source="node.api",
                ts=time.time(),
            )
        )
    except Exception:
        _log.debug("failed to publish %s for action=%s webspace=%s", event_type, action, webspace_id, exc_info=True)


def _request_client_label(request: Request, *, endpoint: str) -> str:
    client = request.client
    host = str(getattr(client, "host", "") or "").strip() or "-"
    port = getattr(client, "port", None)
    remote = f"{host}:{port}" if port is not None else host
    return f"http:{endpoint}:{remote}"


def _trace_yjs_control_ingress(
    *,
    request: Request,
    kind: str,
    webspace_id: str,
    scenario_id: str | None = None,
    recreate_room: bool = False,
) -> dict[str, Any]:
    endpoint = str(request.url.path or "").strip() or "/api/node/yjs"
    payload: dict[str, Any] = {"webspace_id": webspace_id}
    if scenario_id:
        payload["scenario_id"] = scenario_id
    if recreate_room:
        payload["recreate_room"] = True
    header_cmd_id = str(request.headers.get("x-request-id") or request.headers.get("x-trace-id") or "").strip()
    cmd_id = header_cmd_id or f"api-{uuid.uuid4().hex[:16]}"
    trace_id = str(request.headers.get("x-trace-id") or request.headers.get("x-request-id") or "").strip() or cmd_id
    meta = {
        "cmd_id": cmd_id,
        "gateway_client": _request_client_label(request, endpoint=endpoint),
        "trace_id": trace_id,
        "device_id": str(request.headers.get("x-adaos-device-id") or "").strip() or None,
    }
    try:
        from adaos.services.yjs.gateway_ws import _record_command_trace

        trace = _record_command_trace(
            kind=kind,
            cmd_id=meta["cmd_id"],
            payload=payload,
            device_id=meta["device_id"],
            webspace_id=webspace_id,
            client_label=meta["gateway_client"],
        )
        meta["gateway_command_seq"] = int(trace.get("seq") or 0)
        meta["gateway_command_fingerprint"] = str(trace.get("fingerprint") or "").strip() or None
        _log.warning(
            "%s ingress via control_api cmd=%s seq=%s webspace=%s client=%s scenario=%s recreate_room=%s dup_recent=%s dup10s=%s fp=%s",
            kind,
            meta["cmd_id"] or "-",
            meta.get("gateway_command_seq") or 0,
            webspace_id,
            meta["gateway_client"] or "-",
            scenario_id or "-",
            "yes" if recreate_room else "no",
            "yes" if trace.get("duplicate_recent") else "no",
            trace.get("duplicate_count_10s") or 0,
            meta.get("gateway_command_fingerprint") or "-",
        )
    except Exception:
        _log.debug("failed to trace %s ingress for webspace=%s", kind, webspace_id, exc_info=True)
    payload["_meta"] = meta
    return payload


def _attach_runtime_and_rebuild(
    result: dict[str, Any],
    *,
    role: str,
    webspace_id: str,
    include_rebuild: bool = False,
) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(result.get("webspace_id") or webspace_id)
    result["runtime"] = yjs_sync_runtime_snapshot(
        role=role,
        webspace_id=target_webspace_id,
    )
    if include_rebuild:
        result["rebuild"] = describe_webspace_rebuild_state(target_webspace_id)
    return result


def _clear_reload_yws_guard_state(webspace_id: str, *, reason: str) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    try:
        from adaos.services.yjs.gateway import clear_yws_guard_state_for_webspace

        return clear_yws_guard_state_for_webspace(target_webspace_id, reason=reason)
    except Exception as exc:
        _log.debug(
            "failed to clear YWS guard recovery state webspace=%s reason=%s",
            target_webspace_id,
            reason,
            exc_info=True,
        )
        return {
            "ok": False,
            "webspace_id": target_webspace_id,
            "reason": str(reason or "").strip() or "manual_webspace_recovery",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _attach_wait_for_rebuild_guard(
    result: dict[str, Any],
    *,
    requested: bool,
    effective: bool,
    reason: str,
) -> dict[str, Any]:
    if requested == effective:
        return result
    guards = result.get("guards")
    if not isinstance(guards, dict):
        guards = {}
        result["guards"] = guards
    guards["wait_for_rebuild"] = {
        "requested": requested,
        "effective": effective,
        "reason": reason,
    }
    return result


def _runtime_debug_slice(runtime: Mapping[str, Any] | None) -> dict[str, Any]:
    runtime_map = dict(runtime) if isinstance(runtime, Mapping) else {}
    transport = runtime_map.get("transport") if isinstance(runtime_map.get("transport"), Mapping) else {}
    assessment = runtime_map.get("assessment") if isinstance(runtime_map.get("assessment"), Mapping) else {}
    selected = runtime_map.get("selected_webspace") if isinstance(runtime_map.get("selected_webspace"), Mapping) else {}
    return {
        "assessment": {
            "state": str(assessment.get("state") or "").strip() or None,
            "reason": str(assessment.get("reason") or "").strip() or None,
        },
        "transport": {
            "active_yws_connections": int(transport.get("active_yws_connections") or 0),
            "active_clients": list(transport.get("active_clients") or []),
            "recent_open_10s": int(transport.get("recent_open_10s") or 0),
            "recent_open_60s": int(transport.get("recent_open_60s") or 0),
            "storm_detected": bool(transport.get("storm_detected")),
            "guard": dict(transport.get("guard") or {}) if isinstance(transport.get("guard"), Mapping) else {},
            "room_total": int(transport.get("room_total") or 0),
            "active_room_total": int(transport.get("active_room_total") or 0),
            "room_reset_total": int(transport.get("room_reset_total") or 0),
            "room_drop_total": int(transport.get("room_drop_total") or 0),
            "room_generation_max": int(transport.get("room_generation_max") or 0),
            "update_stream_buffer_used_total": int(transport.get("update_stream_buffer_used_total") or 0),
            "update_stream_waiting_send_total": int(transport.get("update_stream_waiting_send_total") or 0),
            "update_stream_waiting_receive_total": int(transport.get("update_stream_waiting_receive_total") or 0),
            "server_ready": bool(transport.get("server_ready")),
            "server_error": str(transport.get("server_error") or "").strip() or None,
        },
        "selected_webspace": {
            "id": str(runtime_map.get("selected_webspace_id") or "").strip() or None,
            "runtime_compaction_eligible": bool(selected.get("runtime_compaction_eligible")),
            "update_log_entries": int(selected.get("update_log_entries") or 0),
            "replay_window_entries": int(selected.get("replay_window_entries") or 0),
            "replay_window_bytes": int(selected.get("replay_window_bytes") or 0),
            "gateway_room": dict(selected.get("gateway_room") or {})
            if isinstance(selected.get("gateway_room"), Mapping)
            else {},
        },
    }


def _attach_yjs_action_debug(
    result: dict[str, Any],
    *,
    requested_endpoint: str,
    recreate_room_requested: bool,
    runtime_before: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    reset_room = result.get("reset_room") if isinstance(result.get("reset_room"), Mapping) else {}
    result["action_debug"] = {
        "requested_endpoint": str(requested_endpoint or "").strip() or None,
        "requested_action": str(result.get("action") or requested_endpoint or "").strip() or None,
        "recreate_room_requested": bool(recreate_room_requested),
        "room_recreated": bool(reset_room.get("room_dropped")),
        "reset_room": dict(reset_room) if reset_room else None,
        "runtime_before": _runtime_debug_slice(runtime_before),
        "runtime_after": _runtime_debug_slice(result.get("runtime")),
    }
    return result


def _collect_materialization_missing_branches(
    *,
    has_ui_application: bool,
    has_desktop_config: bool,
    has_desktop_page_schema: bool,
    has_apps_catalog_modal: bool,
    has_widgets_catalog_modal: bool,
    has_catalog_apps: bool,
    has_catalog_widgets: bool,
    has_data_desktop: bool,
    has_installed_apps: bool,
    has_installed_widgets: bool,
) -> list[str]:
    missing: list[str] = []
    if not has_ui_application:
        missing.append("ui.application")
    if not has_desktop_config:
        missing.append("ui.application.desktop")
    if not has_desktop_page_schema:
        missing.append("ui.application.desktop.pageSchema")
    if not has_apps_catalog_modal:
        missing.append("ui.application.modals.apps_catalog")
    if not has_widgets_catalog_modal:
        missing.append("ui.application.modals.widgets_catalog")
    if not has_catalog_apps:
        missing.append("data.catalog.apps")
    if not has_catalog_widgets:
        missing.append("data.catalog.widgets")
    if not has_data_desktop:
        missing.append("data.desktop")
    if not has_installed_apps:
        missing.append("data.installed.apps")
    if not has_installed_widgets:
        missing.append("data.installed.widgets")
    return missing


def _derive_materialization_readiness_state(
    *,
    ready: bool,
    current_scenario: str | None,
    has_ui_application: bool,
    has_desktop_config: bool,
    has_desktop_page_schema: bool,
    has_apps_catalog_modal: bool,
    has_widgets_catalog_modal: bool,
    has_catalog_apps: bool,
    has_catalog_widgets: bool,
    has_data_desktop: bool,
    has_installed_apps: bool,
    has_installed_widgets: bool,
) -> str:
    if ready:
        return "ready"
    has_effective_data = has_data_desktop and has_installed_apps and has_installed_widgets
    if has_desktop_page_schema and has_catalog_apps and has_catalog_widgets and has_effective_data:
        return "interactive"
    if has_desktop_page_schema and (
        has_catalog_apps
        or has_catalog_widgets
        or has_apps_catalog_modal
        or has_widgets_catalog_modal
        or has_effective_data
    ):
        return "hydrating"
    if has_desktop_page_schema:
        return "first_paint"
    if current_scenario or has_ui_application or has_desktop_config:
        return "pending_structure"
    return "degraded"


def _collect_compatibility_cache_required_branches(current_scenario: str | None) -> list[str]:
    scenario_id = str(current_scenario or "").strip()
    if not scenario_id:
        return []
    node_id = _local_node_id()
    return [
        f"ui.scenarios.{node_id}.{scenario_id}.application",
        f"registry.scenarios.{node_id}.{scenario_id}",
        f"data.scenarios.{node_id}.{scenario_id}.catalog",
    ]


def _describe_compatibility_caches(
    *,
    current_scenario: str | None,
    has_scenario_ui_application: bool,
    has_scenario_registry_entry: bool,
    has_scenario_catalog: bool,
    effective_ready: bool,
    rebuild_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    required_branches = _collect_compatibility_cache_required_branches(current_scenario)
    present_flags = (
        has_scenario_ui_application,
        has_scenario_registry_entry,
        has_scenario_catalog,
    )
    present_branches = [path for path, present in zip(required_branches, present_flags) if present]
    missing_branches = [path for path, present in zip(required_branches, present_flags) if not present]
    resolver = (
        rebuild_state.get("resolver")
        if isinstance(rebuild_state, Mapping) and isinstance(rebuild_state.get("resolver"), Mapping)
        else {}
    )
    legacy_fallback_active = bool(resolver.get("legacy_fallback"))
    switch_writes_enabled = False
    runtime_removal_blockers: list[str] = []
    if not str(current_scenario or "").strip():
        runtime_removal_blockers.append("current_scenario_missing")
    if not effective_ready:
        runtime_removal_blockers.append("effective_materialization_not_ready")
    if legacy_fallback_active:
        runtime_removal_blockers.append("resolver_legacy_fallback_active")
    return {
        "current_scenario": str(current_scenario or "").strip() or None,
        "required_branches": required_branches,
        "present_branches": present_branches,
        "missing_branches": missing_branches,
        "present_count": len(present_branches),
        "required_count": len(required_branches),
        "present": bool(present_branches),
        "complete": bool(required_branches) and not missing_branches,
        "client_fallback_readable": bool(str(current_scenario or "").strip() and has_scenario_ui_application),
        "switch_writes_enabled": switch_writes_enabled,
        "legacy_fallback_active": legacy_fallback_active,
        "runtime_removal_ready": not runtime_removal_blockers,
        "runtime_removal_blockers": runtime_removal_blockers,
    }


def _cached_materialization_from_rebuild(
    rebuild_state: Mapping[str, Any] | None,
    *,
    max_age_sec: float | None = None,
) -> dict[str, Any] | None:
    state = rebuild_state if isinstance(rebuild_state, Mapping) else {}
    cached = state.get("materialization") if isinstance(state.get("materialization"), Mapping) else {}
    if not cached:
        return None
    if max_age_sec is None:
        try:
            max_age_sec = float(os.getenv("ADAOS_YJS_MATERIALIZATION_CACHE_MAX_AGE_SEC", "3") or "3")
        except Exception:
            max_age_sec = 3.0
    pending = bool(state.get("pending"))
    observed_at = cached.get("observed_at")
    try:
        age_sec = max(0.0, time.time() - float(observed_at)) if observed_at is not None else None
    except Exception:
        age_sec = None
    result = dict(cached)
    result["snapshot_source"] = "rebuild_cache"
    max_age = max(float(max_age_sec or 0.0), 0.0)
    result["cache_ttl_s"] = round(max_age, 3)
    if age_sec is not None:
        result["cache_age_s"] = round(age_sec, 3)
    stale_by_age = age_sec is not None and age_sec > max_age
    if pending:
        result["stale"] = True
        if not str(result.get("stale_reason") or "").strip():
            result["stale_reason"] = "rebuild_pending"
    if stale_by_age:
        result["stale"] = True
        if not str(result.get("stale_reason") or "").strip():
            result["stale_reason"] = "rebuild_cache_ttl_exceeded"
    result["cache_fresh"] = not bool(result.get("stale")) and not stale_by_age
    return result


def _missing_materialization_cache_snapshot(
    webspace_id: str,
    *,
    rebuild_state: Mapping[str, Any] | None = None,
    stale_reason: str = "rebuild_cache_missing",
) -> dict[str, Any]:
    state = rebuild_state if isinstance(rebuild_state, Mapping) else {}
    cached = state.get("materialization") if isinstance(state.get("materialization"), Mapping) else {}
    current_scenario = (
        str(state.get("scenario_id") or "").strip()
        or str(cached.get("current_scenario") or "").strip()
        or None
    )
    missing_branches = _collect_materialization_missing_branches(
        has_ui_application=False,
        has_desktop_config=False,
        has_desktop_page_schema=False,
        has_apps_catalog_modal=False,
        has_widgets_catalog_modal=False,
        has_catalog_apps=False,
        has_catalog_widgets=False,
        has_data_desktop=False,
        has_installed_apps=False,
        has_installed_widgets=False,
    )
    compatibility_caches = _describe_compatibility_caches(
        current_scenario=current_scenario,
        has_scenario_ui_application=False,
        has_scenario_registry_entry=False,
        has_scenario_catalog=False,
        effective_ready=False,
        rebuild_state=rebuild_state,
    )
    return {
        "ready": False,
        "readiness_state": "status_cache_missing",
        "missing_branches": missing_branches,
        "compatibility_caches": compatibility_caches,
        "webspace_id": _coerce_node_webspace_id(webspace_id),
        "current_scenario": current_scenario,
        "has_ui_application": False,
        "has_desktop_config": False,
        "has_desktop_page_schema": False,
        "has_apps_catalog_modal": False,
        "has_widgets_catalog_modal": False,
        "has_catalog_apps": False,
        "has_catalog_widgets": False,
        "has_data_desktop": False,
        "has_installed_apps": False,
        "has_installed_widgets": False,
        "catalog_counts": {"apps": 0, "widgets": 0},
        "installed_counts": {"apps": 0, "widgets": 0},
        "topbar_count": 0,
        "page_widget_count": 0,
        "snapshot_source": "rebuild_cache_missing",
        "observed_at": time.time(),
        "stale": True,
        "stale_reason": str(stale_reason or "").strip() or "rebuild_cache_missing",
        "cache_fresh": False,
    }


async def _describe_yjs_materialization(
    webspace_id: str,
    *,
    rebuild_state: Mapping[str, Any] | None = None,
    verify_live: bool = False,
) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    cached = _cached_materialization_from_rebuild(rebuild_state)
    if cached and not verify_live:
        return cached
    if not verify_live:
        return _missing_materialization_cache_snapshot(
            target_webspace_id,
            rebuild_state=rebuild_state,
        )
    try:
        async with async_read_ydoc(target_webspace_id, prefer_live_room=True) as ydoc:
            ui_map = ydoc.get_map("ui")
            data_map = ydoc.get_map("data")
            registry_map = ydoc.get_map("registry")
            application = _coerce_dict(ui_map.get("application") or {})
            desktop = _coerce_dict(application.get("desktop") or {})
            modals = _coerce_dict(application.get("modals") or {})
            catalog = _coerce_dict(data_map.get("catalog") or {})
            apps = _coerce_list(catalog.get("apps"))
            widgets = _coerce_list(catalog.get("widgets"))
            data_desktop_raw = data_map.get("desktop")
            installed_raw = data_map.get("installed")
            installed = _coerce_dict(installed_raw or {})
            installed_apps = _coerce_list(installed.get("apps"))
            installed_widgets = _coerce_list(installed.get("widgets"))
            page_schema = _coerce_dict(desktop.get("pageSchema") or {})
            page_widgets = _coerce_list(page_schema.get("widgets"))
            topbar = _coerce_list(desktop.get("topbar"))
            current_scenario = str(ui_map.get("current_scenario") or "").strip() or None
            scenarios_ui = _coerce_dict(ui_map.get("scenarios") or {})
            scenario_ui_entry = _read_node_scoped_scenario_entry(scenarios_ui, current_scenario) if current_scenario else {}
            scenario_ui_application = _coerce_dict(scenario_ui_entry.get("application") or {})
            scenario_registry_map = _coerce_dict(registry_map.get("scenarios") or {})
            scenario_registry_entry = _read_node_scoped_scenario_entry(scenario_registry_map, current_scenario) if current_scenario else {}
            scenario_data_map = _coerce_dict(data_map.get("scenarios") or {})
            scenario_data_entry = _read_node_scoped_scenario_entry(scenario_data_map, current_scenario) if current_scenario else {}
            scenario_catalog = _coerce_dict(scenario_data_entry.get("catalog") or {})

            has_ui_application = bool(application)
            has_desktop_config = bool(desktop)
            has_desktop_page_schema = bool(page_schema)
            has_apps_catalog_modal = "apps_catalog" in modals
            has_widgets_catalog_modal = "widgets_catalog" in modals
            has_catalog_apps = isinstance(catalog.get("apps"), list)
            has_catalog_widgets = isinstance(catalog.get("widgets"), list)
            has_data_desktop = isinstance(_clone_json_like(data_desktop_raw), dict)
            has_installed_apps = isinstance(installed.get("apps"), list)
            has_installed_widgets = isinstance(installed.get("widgets"), list)
            missing_branches = _collect_materialization_missing_branches(
                has_ui_application=has_ui_application,
                has_desktop_config=has_desktop_config,
                has_desktop_page_schema=has_desktop_page_schema,
                has_apps_catalog_modal=has_apps_catalog_modal,
                has_widgets_catalog_modal=has_widgets_catalog_modal,
                has_catalog_apps=has_catalog_apps,
                has_catalog_widgets=has_catalog_widgets,
                has_data_desktop=has_data_desktop,
                has_installed_apps=has_installed_apps,
                has_installed_widgets=has_installed_widgets,
            )
            ready = not missing_branches
            readiness_state = _derive_materialization_readiness_state(
                ready=ready,
                current_scenario=current_scenario,
                has_ui_application=has_ui_application,
                has_desktop_config=has_desktop_config,
                has_desktop_page_schema=has_desktop_page_schema,
                has_apps_catalog_modal=has_apps_catalog_modal,
                has_widgets_catalog_modal=has_widgets_catalog_modal,
                has_catalog_apps=has_catalog_apps,
                has_catalog_widgets=has_catalog_widgets,
                has_data_desktop=has_data_desktop,
                has_installed_apps=has_installed_apps,
                has_installed_widgets=has_installed_widgets,
            )
            compatibility_caches = _describe_compatibility_caches(
                current_scenario=current_scenario,
                has_scenario_ui_application=bool(scenario_ui_application),
                has_scenario_registry_entry=bool(scenario_registry_entry),
                has_scenario_catalog=bool(scenario_catalog),
                effective_ready=ready,
                rebuild_state=rebuild_state,
            )

            return {
                "ready": ready,
                "readiness_state": readiness_state,
                "missing_branches": missing_branches,
                "compatibility_caches": compatibility_caches,
                "webspace_id": target_webspace_id,
                "current_scenario": current_scenario,
                "has_ui_application": has_ui_application,
                "has_desktop_config": has_desktop_config,
                "has_desktop_page_schema": has_desktop_page_schema,
                "has_apps_catalog_modal": has_apps_catalog_modal,
                "has_widgets_catalog_modal": has_widgets_catalog_modal,
                "has_catalog_apps": has_catalog_apps,
                "has_catalog_widgets": has_catalog_widgets,
                "has_data_desktop": has_data_desktop,
                "has_installed_apps": has_installed_apps,
                "has_installed_widgets": has_installed_widgets,
                "catalog_counts": {
                    "apps": len(apps),
                    "widgets": len(widgets),
                },
                "installed_counts": {
                    "apps": len(installed_apps),
                    "widgets": len(installed_widgets),
                },
                "topbar_count": len(topbar),
                "page_widget_count": len(page_widgets),
                "snapshot_source": "live_ydoc_verification",
                "observed_at": time.time(),
                "stale": False,
            }
    except Exception as exc:
        missing_branches = _collect_materialization_missing_branches(
            has_ui_application=False,
            has_desktop_config=False,
            has_desktop_page_schema=False,
            has_apps_catalog_modal=False,
            has_widgets_catalog_modal=False,
            has_catalog_apps=False,
            has_catalog_widgets=False,
            has_data_desktop=False,
            has_installed_apps=False,
            has_installed_widgets=False,
        )
        compatibility_caches = _describe_compatibility_caches(
            current_scenario=None,
            has_scenario_ui_application=False,
            has_scenario_registry_entry=False,
            has_scenario_catalog=False,
            effective_ready=False,
            rebuild_state=rebuild_state,
        )
        return {
            "ready": False,
            "readiness_state": "degraded",
            "missing_branches": missing_branches,
            "compatibility_caches": compatibility_caches,
            "webspace_id": target_webspace_id,
            "current_scenario": None,
            "has_ui_application": False,
            "has_desktop_config": False,
            "has_desktop_page_schema": False,
            "has_apps_catalog_modal": False,
            "has_widgets_catalog_modal": False,
            "has_catalog_apps": False,
            "has_catalog_widgets": False,
            "has_data_desktop": False,
            "has_installed_apps": False,
            "has_installed_widgets": False,
            "catalog_counts": {"apps": 0, "widgets": 0},
            "installed_counts": {"apps": 0, "widgets": 0},
            "topbar_count": 0,
            "page_widget_count": 0,
            "snapshot_source": "live_ydoc_verification_error",
            "observed_at": time.time(),
            "stale": True,
            "error": f"{exc.__class__.__name__}: {exc}",
        }


async def _read_yjs_materialization_snapshot(
    webspace_id: str,
    *,
    scope: str = "essential",
    prefer_live_room: bool = False,
) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    normalized_scope = str(scope or "").strip().lower() or "essential"
    async with async_read_ydoc(target_webspace_id, prefer_live_room=prefer_live_room) as ydoc:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        registry_map = ydoc.get_map("registry")
        if normalized_scope != "full":
            return {
                "ui": {
                    "current_scenario": _clone_json_like(ui_map.get("current_scenario")),
                    "application": _coerce_dict(_clone_json_like(ui_map.get("application") or {})),
                },
                "data": {
                    "catalog": _coerce_dict(_clone_json_like(data_map.get("catalog") or {})),
                    "desktop": _coerce_dict(_clone_json_like(data_map.get("desktop") or {})),
                    "installed": _coerce_dict(_clone_json_like(data_map.get("installed") or {})),
                    "nodes": _coerce_dict(_clone_json_like(data_map.get("nodes") or {})),
                    "webspaces": _coerce_dict(_clone_json_like(data_map.get("webspaces") or {})),
                },
                "registry": {},
            }
        return {
            "ui": _coerce_dict(_clone_json_like(ui_map)),
            "data": _coerce_dict(_clone_json_like(data_map)),
            "registry": _coerce_dict(_clone_json_like(registry_map)),
        }


def _materialized_payload_to_snapshot(
    webspace_id: str,
    payload: Mapping[str, Any] | None,
    *,
    scope: str = "essential",
) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping) or not payload:
        return None
    scenario_id = str(payload.get("scenario_id") or "").strip()
    if not scenario_id:
        return None
    metadata = _coerce_dict(payload.get("metadata") or {})
    materialization = _coerce_dict(metadata.get("materialization") or {})
    materialization["scenario_id"] = str(materialization.get("scenario_id") or scenario_id).strip() or scenario_id
    runtime = {
        "environment": {
            "materialization": materialization,
        },
    }
    ui = {
        "current_scenario": scenario_id,
        "application": _coerce_dict(_clone_json_like(payload.get("application") or {})),
    }
    data = {
        "catalog": _coerce_dict(_clone_json_like(payload.get("catalog") or {})),
        "desktop": _coerce_dict(_clone_json_like(payload.get("desktop") or {})),
        "installed": _coerce_dict(_clone_json_like(payload.get("installed") or {})),
        "nodes": {},
        "webspaces": {},
        "webio": _coerce_dict(_clone_json_like(payload.get("webio") or {})),
        "routing": _coerce_dict(_clone_json_like(payload.get("routing") or {})),
    }
    registry_payload = _coerce_dict(_clone_json_like(payload.get("registry") or {}))
    registry = {"merged": registry_payload} if registry_payload else {}
    if str(scope or "").strip().lower() == "full":
        return {
            "ui": ui,
            "data": data,
            "registry": registry,
            "runtime": runtime,
        }
    return {
        "ui": ui,
        "data": data,
        "registry": registry,
        "runtime": runtime,
    }


def _describe_materialization_snapshot_payload(
    webspace_id: str,
    snapshot: Mapping[str, Any] | None,
    *,
    rebuild_state: Mapping[str, Any] | None = None,
    source: str = "disk_snapshot",
) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    payload = snapshot if isinstance(snapshot, Mapping) else {}
    ui = _coerce_dict(payload.get("ui") or {})
    data = _coerce_dict(payload.get("data") or {})
    registry = _coerce_dict(payload.get("registry") or {})
    application = _coerce_dict(ui.get("application") or {})
    desktop = _coerce_dict(application.get("desktop") or {})
    modals = _coerce_dict(application.get("modals") or {})
    catalog = _coerce_dict(data.get("catalog") or {})
    apps = _coerce_list(catalog.get("apps"))
    widgets = _coerce_list(catalog.get("widgets"))
    data_desktop_raw = data.get("desktop")
    installed_raw = data.get("installed")
    installed = _coerce_dict(installed_raw or {})
    installed_apps = _coerce_list(installed.get("apps"))
    installed_widgets = _coerce_list(installed.get("widgets"))
    page_schema = _coerce_dict(desktop.get("pageSchema") or {})
    page_widgets = _coerce_list(page_schema.get("widgets"))
    topbar = _coerce_list(desktop.get("topbar"))
    current_scenario = str(ui.get("current_scenario") or "").strip() or None
    scenarios_ui = _coerce_dict(ui.get("scenarios") or {})
    scenario_ui_entry = _read_node_scoped_scenario_entry(scenarios_ui, current_scenario) if current_scenario else {}
    scenario_ui_application = _coerce_dict(scenario_ui_entry.get("application") or {})
    scenario_registry_map = _coerce_dict(registry.get("scenarios") or {})
    scenario_registry_entry = _read_node_scoped_scenario_entry(scenario_registry_map, current_scenario) if current_scenario else {}
    scenario_data_map = _coerce_dict(data.get("scenarios") or {})
    scenario_data_entry = _read_node_scoped_scenario_entry(scenario_data_map, current_scenario) if current_scenario else {}
    scenario_catalog = _coerce_dict(scenario_data_entry.get("catalog") or {})

    has_ui_application = bool(application)
    has_desktop_config = bool(desktop)
    has_desktop_page_schema = bool(page_schema)
    has_apps_catalog_modal = "apps_catalog" in modals
    has_widgets_catalog_modal = "widgets_catalog" in modals
    has_catalog_apps = isinstance(catalog.get("apps"), list)
    has_catalog_widgets = isinstance(catalog.get("widgets"), list)
    has_data_desktop = isinstance(data_desktop_raw, dict)
    has_installed_apps = isinstance(installed.get("apps"), list)
    has_installed_widgets = isinstance(installed.get("widgets"), list)
    missing_branches = _collect_materialization_missing_branches(
        has_ui_application=has_ui_application,
        has_desktop_config=has_desktop_config,
        has_desktop_page_schema=has_desktop_page_schema,
        has_apps_catalog_modal=has_apps_catalog_modal,
        has_widgets_catalog_modal=has_widgets_catalog_modal,
        has_catalog_apps=has_catalog_apps,
        has_catalog_widgets=has_catalog_widgets,
        has_data_desktop=has_data_desktop,
        has_installed_apps=has_installed_apps,
        has_installed_widgets=has_installed_widgets,
    )
    ready = not missing_branches
    readiness_state = _derive_materialization_readiness_state(
        ready=ready,
        current_scenario=current_scenario,
        has_ui_application=has_ui_application,
        has_desktop_config=has_desktop_config,
        has_desktop_page_schema=has_desktop_page_schema,
        has_apps_catalog_modal=has_apps_catalog_modal,
        has_widgets_catalog_modal=has_widgets_catalog_modal,
        has_catalog_apps=has_catalog_apps,
        has_catalog_widgets=has_catalog_widgets,
        has_data_desktop=has_data_desktop,
        has_installed_apps=has_installed_apps,
        has_installed_widgets=has_installed_widgets,
    )
    compatibility_caches = _describe_compatibility_caches(
        current_scenario=current_scenario,
        has_scenario_ui_application=bool(scenario_ui_application),
        has_scenario_registry_entry=bool(scenario_registry_entry),
        has_scenario_catalog=bool(scenario_catalog),
        effective_ready=ready,
        rebuild_state=rebuild_state,
    )
    return {
        "ready": ready,
        "readiness_state": readiness_state,
        "missing_branches": missing_branches,
        "compatibility_caches": compatibility_caches,
        "webspace_id": target_webspace_id,
        "current_scenario": current_scenario,
        "has_ui_application": has_ui_application,
        "has_desktop_config": has_desktop_config,
        "has_desktop_page_schema": has_desktop_page_schema,
        "has_apps_catalog_modal": has_apps_catalog_modal,
        "has_widgets_catalog_modal": has_widgets_catalog_modal,
        "has_catalog_apps": has_catalog_apps,
        "has_catalog_widgets": has_catalog_widgets,
        "has_data_desktop": has_data_desktop,
        "has_installed_apps": has_installed_apps,
        "has_installed_widgets": has_installed_widgets,
        "catalog_counts": {
            "apps": len(apps),
            "widgets": len(widgets),
        },
        "installed_counts": {
            "apps": len(installed_apps),
            "widgets": len(installed_widgets),
        },
        "topbar_count": len(topbar),
        "page_widget_count": len(page_widgets),
        "snapshot_source": source,
        "observed_at": time.time(),
        "stale": not ready,
        "stale_reason": "" if ready else "disk_snapshot_missing_required_branches",
        "cache_fresh": ready,
    }


def _materialization_seed_health(
    *,
    state: str,
    reason: str,
    source: str,
    stale: bool,
    last_good_snapshot_at: Any = None,
    timeout_s: float | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    payload = {
        "state": str(state or "").strip() or "unknown",
        "reason": str(reason or "").strip() or "unknown",
        "source": str(source or "").strip() or "none",
        "stale": bool(stale),
        "last_good_snapshot_at": last_good_snapshot_at,
        "timeout_s": round(float(timeout_s), 3) if timeout_s is not None else None,
    }
    if error:
        payload["error"] = str(error).strip()[:240]
    return payload


def _fallback_materialization_snapshot_from_cache(
    webspace_id: str,
    *,
    rebuild_state: Mapping[str, Any] | None,
    reason: str,
    error: str | None = None,
) -> dict[str, Any]:
    cached = _cached_materialization_from_rebuild(rebuild_state, max_age_sec=0.0)
    materialization = cached or _missing_materialization_cache_snapshot(
        webspace_id,
        rebuild_state=rebuild_state,
        stale_reason=reason,
    )
    materialization = dict(materialization)
    materialization["ready"] = False
    materialization["stale"] = True
    materialization["stale_reason"] = reason
    materialization["snapshot_source"] = materialization.get("snapshot_source") or "rebuild_cache"
    source = "rebuild_cache" if cached else "none"
    last_good = (
        materialization.get("observed_at")
        or (rebuild_state or {}).get("finished_at")
        or (rebuild_state or {}).get("updated_at")
    )
    return {
        "snapshot": {"ui": {}, "data": {}, "registry": {}},
        "materialization": materialization,
        "seed_health": _materialization_seed_health(
            state="degraded",
            reason=reason,
            source=source,
            stale=True,
            last_good_snapshot_at=last_good,
            timeout_s=_YJS_MATERIALIZATION_SNAPSHOT_TIMEOUT_S,
            error=error,
        ),
    }


async def _read_live_catalog_items(webspace_id: str, kind: str) -> list[dict[str, Any]]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    bucket = "widgets" if str(kind or "").strip().lower() == "widgets" else "apps"
    try:
        async with async_read_ydoc(target_webspace_id) as ydoc:
            data_map = ydoc.get_map("data")
            catalog = _coerce_dict(_clone_json_like(data_map.get("catalog") or {}))
            items = _clone_json_like(catalog.get(bucket))
            return [dict(it) for it in _coerce_list(items) if isinstance(it, dict)]
    except Exception:
        return []


async def _materialize_catalog_items(webspace_id: str, kind: str) -> list[dict[str, Any]]:
    bucket = "widgets" if str(kind or "").strip().lower() == "widgets" else "apps"
    raw_items = await _read_live_catalog_items(webspace_id, bucket)
    if not raw_items:
        try:
            operational_state = await describe_webspace_operational_state(webspace_id)
            expected_scenario = (
                str(getattr(operational_state, "current_scenario", None) or "").strip()
                or str(getattr(operational_state, "effective_home_scenario", None) or "").strip()
            )
            payload_snapshot = _materialized_payload_to_snapshot(
                webspace_id,
                get_webspace_rebuild_materialized_payload(webspace_id),
                scope="full",
            )
            payload_ui = _coerce_dict(_coerce_dict(payload_snapshot or {}).get("ui") or {})
            payload_scenario = str(payload_ui.get("current_scenario") or "").strip()
            payload_data = _coerce_dict(_coerce_dict(payload_snapshot or {}).get("data") or {})
            payload_catalog = _coerce_dict(payload_data.get("catalog") or {})
            payload_items = _coerce_list(payload_catalog.get(bucket))
            if expected_scenario and payload_scenario == expected_scenario and payload_items:
                raw_items = [dict(it) for it in payload_items if isinstance(it, dict)]
        except Exception:
            raw_items = []
    desktop_snapshot = await WebDesktopService().get_snapshot_async(webspace_id)
    installed_ids = set(
        list(getattr(getattr(desktop_snapshot, "installed", None), "apps", []) or [])
        if bucket == "apps"
        else list(getattr(getattr(desktop_snapshot, "installed", None), "widgets", []) or [])
    )
    pinned_ids = {
        str(item.get("id") or "").strip()
        for item in list(getattr(desktop_snapshot, "pinned_widgets", []) or [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    default_icon = "apps-outline" if bucket == "apps" else "layers-outline"
    materialized: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip()
        if not item_id:
            continue
        scenario_id = str(raw.get("scenario_id") or "").strip()
        launch_modal = str(raw.get("launchModal") or "").strip()
        source = str(raw.get("source") or raw.get("origin") or "").strip()
        installed_now = item_id in installed_ids
        pinned_now = bucket == "widgets" and item_id in pinned_ids
        kind_label = ""
        if scenario_id:
            kind_label = "Scenario"
        elif launch_modal:
            kind_label = "Modal"
        elif bucket == "widgets":
            kind_label = "Widget"
        materialized.append(
            {
                "id": item_id,
                "title": str(raw.get("title") or item_id).strip() or item_id,
                "icon": str(raw.get("icon") or "").strip() or default_icon,
                "subtitle": str(raw.get("subtitle") or "").strip() or scenario_id or launch_modal or source or "",
                "kindLabel": kind_label,
                "installType": "app" if bucket == "apps" else "widget",
                "installable": True,
                "installed": installed_now,
                "pinnable": bucket == "widgets" and (installed_now or pinned_now),
                "pinned": pinned_now,
                "scenario_id": scenario_id or None,
                "launchModal": launch_modal or None,
                "source": source or None,
                "origin": str(raw.get("origin") or "").strip() or None,
                "dev": bool(raw.get("dev")),
                "node_id": str(raw.get("node_id") or "").strip() or None,
                "node_label": str(raw.get("node_label") or "").strip() or None,
                "node_compact_label": str(raw.get("node_compact_label") or "").strip() or None,
                "node_color": str(raw.get("node_color") or "").strip() or None,
                "node_index": _coerce_optional_int(raw.get("node_index")),
                "node_local_id": str(raw.get("node_local_id") or raw.get("remote_id") or "").strip() or None,
            }
        )
    return materialized


class NodeStatus(BaseModel):
    node_id: str
    subnet_id: str
    role: str
    node_names: list[str] = Field(default_factory=list)
    primary_node_name: str = ""
    node_label: str = ""
    node_compact_label: str = ""
    node_index: int | None = None
    node_color: str | None = None
    ready: bool
    node_state: str = "ready"
    draining: bool = False
    route_mode: Optional[str] = None
    connected_to_subnet: Optional[bool] = None
    connected_to_hub: Optional[bool] = None
    status_profile: str = "transport"
    runtime: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)


class RoleChangeRequest(BaseModel):
    role: str = Field(..., pattern="^(hub|member)$")
    hub_url: Optional[str] = None  # deprecated; ignored
    subnet_id: Optional[str] = None


class RoleChangeResponse(BaseModel):
    ok: bool
    node: NodeStatus
    diagnostics: dict


class HubRootReconnectRequest(BaseModel):
    transport: Optional[str] = Field(None, pattern="^(ws|tcp|nats)?$")
    url_override: Optional[str] = None


class MemberHubReconnectRequest(BaseModel):
    force: bool = False


class MemberHubRefreshRequest(BaseModel):
    reason: str = Field(default="member_hub_refresh", min_length=1, max_length=128)


class HubRootRouteResetRequest(BaseModel):
    reason: str | None = None
    notify_browser: bool = True


class SidecarRestartRequest(BaseModel):
    reconnect_hub_root: bool = True
    allow_active_channel_disruption: bool = False


class NodeNamesUpdateRequest(BaseModel):
    node_names: list[str] | None = None
    value: str | None = None


class MemberUpdateRequest(BaseModel):
    action: str = Field(..., pattern="^(update|start|cancel|rollback)$")
    target_rev: str | None = None
    target_version: str | None = None
    countdown_sec: float | None = None
    drain_timeout_sec: float | None = None
    signal_delay_sec: float | None = None
    reason: str | None = None


class WebspaceYjsActionRequest(BaseModel):
    scenario_id: str | None = None
    scenario_ref: dict[str, Any] | None = None
    home_scenario_ref: dict[str, Any] | None = None
    set_home: bool | None = None
    wait_for_rebuild: bool | None = None
    include_runtime: bool | None = None
    include_rebuild: bool | None = None
    recreate_room: bool | None = None
    requested_id: str | None = None
    title: str | None = None
    request_id: str | None = None
    request_source: str | None = None


class WebspaceMaterializationRepairRequest(BaseModel):
    expected_scenario: str | None = Field(default=None, max_length=256)
    missing_branches: list[str] = Field(default_factory=list, max_length=32)
    request_id: str | None = Field(default=None, max_length=256)
    request_source: str | None = Field(default=None, max_length=256)


_YJS_MATERIALIZATION_REPAIR_LOCK = threading.RLock()
_YJS_MATERIALIZATION_REPAIR_INFLIGHT: dict[
    str,
    tuple[asyncio.AbstractEventLoop, asyncio.Task[dict[str, Any]]],
] = {}


async def _coalesced_materialization_repair(
    webspace_id: str,
    materialized_payload: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    from adaos.services.yjs.gateway import apply_materialized_payload_to_live_room

    key = str(webspace_id or "").strip() or "default"
    loop = asyncio.get_running_loop()
    created = False
    with _YJS_MATERIALIZATION_REPAIR_LOCK:
        current = _YJS_MATERIALIZATION_REPAIR_INFLIGHT.get(key)
        if current is not None and current[0] is loop and not current[1].done():
            task = current[1]
        else:
            task = loop.create_task(
                apply_materialized_payload_to_live_room(
                    key,
                    materialized_payload=materialized_payload,
                    reason="client_materialization_repair",
                    persist_repair=False,
                    force_full_state_update=True,
                ),
                name=f"yjs-materialization-repair:{key}",
            )
            _YJS_MATERIALIZATION_REPAIR_INFLIGHT[key] = (loop, task)
            created = True
    try:
        return dict(await asyncio.shield(task)), not created
    finally:
        if task.done():
            with _YJS_MATERIALIZATION_REPAIR_LOCK:
                current = _YJS_MATERIALIZATION_REPAIR_INFLIGHT.get(key)
                if current is not None and current[1] is task:
                    _YJS_MATERIALIZATION_REPAIR_INFLIGHT.pop(key, None)


class WebspaceCreateRequest(BaseModel):
    id: str | None = None
    title: str | None = None
    scenario_id: str | None = None
    scenario_ref: dict[str, Any] | None = None
    dev: bool = False


class WebspaceUpdateRequest(BaseModel):
    title: str | None = None
    home_scenario: str | None = None
    home_scenario_ref: dict[str, Any] | None = None


class WebspaceToggleInstallRequest(BaseModel):
    type: str = Field(..., pattern="^(app|widget)$")
    id: str = Field(..., min_length=1)


class WebspacePinnedWidgetsRequest(BaseModel):
    pinnedWidgets: list[dict[str, Any]] = Field(default_factory=list)


class WebspaceDesktopUpdateRequest(BaseModel):
    installed: dict[str, Any] | None = None
    pinnedWidgets: list[dict[str, Any]] | None = None
    topbar: list[Any] | None = None
    pageSchema: dict[str, Any] | None = None
    iconOrder: list[str] | None = None
    widgetOrder: list[str] | None = None
    hiddenSections: list[str] | None = None


class InfrastateActionRequest(BaseModel):
    id: str = Field(..., min_length=1)
    name: str | None = None
    request_id: str | None = None
    webspace_id: str | None = None
    node_id: str | None = None
    target_node_id: str | None = None
    value: Any | None = None


class InfraAccessActionRequest(BaseModel):
    id: str = Field(..., min_length=1)
    webspace_id: str | None = None
    target_id: str | None = None
    capability_profile: str | None = None
    ttl_seconds: int | None = None


class SkillEventPublishRequest(BaseModel):
    event_type: str | None = None
    type: str | None = None
    payload: Any | None = None
    webspace_id: str | None = None
    workspace_id: str | None = None
    node_id: str | None = None
    target_node_id: str | None = None
    meta: dict[str, Any] | None = Field(default=None, alias="_meta")


class UiRuntimeDiagnosticsRequest(BaseModel):
    webspace_id: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)


class ClientProjectionDemandRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    device_id: str = ""
    session_id: str = Field(..., min_length=1)
    webspace_id: str | None = None
    role: str = "operator"
    subscriptions: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: float | None = None


class ClientProjectionDemandTouchRequest(BaseModel):
    device_id: str | None = None
    role: str | None = None
    updated_at: float | None = None


class BrowserProjectionDemandStateRequest(BaseModel):
    client_id: str = Field(..., min_length=1)
    device_id: str = ""
    session_id: str = Field(..., min_length=1)
    webspace_id: str | None = None
    role: str = "operator"
    page: dict[str, Any] | str | None = None
    widgets: list[dict[str, Any] | str] = Field(default_factory=list)
    modals: list[dict[str, Any] | str] = Field(default_factory=list)
    pinnedPanels: list[dict[str, Any] | str] = Field(default_factory=list)
    pinned_panels: list[dict[str, Any] | str] | None = None
    updated_at: float | None = None


class ProjectionDispatchRequest(BaseModel):
    type: str = Field(..., min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "api.node"
    ts: float | None = None
    webspace_ids: list[str] | None = None
    projection_keys: list[str] | None = None


class ProjectionRecordsYjsMaterializeRequest(BaseModel):
    webspace_id: str | None = None
    projection_keys: list[str] | None = None
    demanded_only: bool = False
    now: float | None = None


class ProjectionDemandYjsMaterializeRequest(BaseModel):
    webspace_id: str | None = None
    include_stale: bool = False
    stale_after_s: float | None = None
    now: float | None = None


class ProjectionDemandYjsRestoreRequest(BaseModel):
    webspace_id: str | None = None
    include_hidden: bool = True
    include_stale: bool = False
    stale_after_s: float | None = None
    now: float | None = None


class PlatformNodeYjsMaterializeRequest(BaseModel):
    webspace_id: str | None = None
    node_id: str | None = None
    status: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None
    projections: dict[str, Any] | None = None
    now: float | None = None


class StatusCardProjectionRecordsMaterializeRequest(BaseModel):
    webspace_id: str | None = None
    card_ids: list[str] | None = None
    demanded_only: bool = False
    write_yjs: bool = False
    now: float | None = None


def _raise_400(detail: str) -> None:
    raise HTTPException(status_code=400, detail=detail)


async def _require_request_token(
    request: Request,
    *,
    authorization: str | None = Header(default=None),
    x_adaos_token: str | None = Header(default=None),
) -> None:
    ensure_token(
        resolve_presented_token(
            x_adaos_token=x_adaos_token,
            authorization=authorization,
            query_token=str(request.query_params.get("token") or "").strip() or None,
        )
    )


def _node_status_payload() -> dict[str, Any]:
    return current_node_status_payload()


@router.get("/status", response_model=NodeStatus, dependencies=[Depends(require_token)])
async def node_status(
    diagnostics: bool = Query(False),
    profile: str = Query("transport", pattern="^(transport|probe)$"),
):
    if profile == "probe":
        return NodeStatus(**current_node_probe_status_payload())
    # Keep the authoritative status shape, but perform its blocking
    # filesystem/SQLite/psutil collection outside the ASGI event loop.
    payload = await asyncio.to_thread(_node_status_payload)
    if not diagnostics:
        payload = compact_node_status_transport_payload(payload)
    return NodeStatus(**payload)


@router.get("/voice/listening", dependencies=[Depends(require_token)])
async def node_voice_listening() -> dict[str, Any]:
    from adaos.services.voice_runtime import (
        get_voice_activation_arbiter,
        listening_service_projection,
        read_voice_policy,
    )

    return {
        "ok": True,
        "policy": await asyncio.to_thread(read_voice_policy),
        "service": await asyncio.to_thread(listening_service_projection),
        "room_arbitration_runtime": await asyncio.to_thread(get_voice_activation_arbiter().snapshot),
    }


@router.post("/voice/listening", dependencies=[Depends(require_token)])
async def node_voice_listening_update(payload: dict[str, Any]) -> dict[str, Any]:
    from adaos.services.voice_runtime import (
        get_voice_activation_arbiter,
        listening_service_projection,
        set_voice_policy,
    )

    mode = str(payload.get("listening_mode") or payload.get("mode") or "").strip()
    try:
        policy = await asyncio.to_thread(
            set_voice_policy,
            listening_mode=mode,
            source=str(payload.get("source") or "node_api"),
            updates=payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "policy": policy,
        "service": listening_service_projection(policy),
        "room_arbitration_runtime": get_voice_activation_arbiter().snapshot(),
    }


@router.get("/control-plane/objects/self", dependencies=[Depends(require_token)])
async def node_control_plane_object_self() -> dict[str, Any]:
    canonical = current_node_object()
    return {"ok": True, "object": canonical.to_dict()}


@router.get("/control-plane/projections/reliability", dependencies=[Depends(require_token)])
async def node_control_plane_reliability_projection(webspace_id: str | None = None) -> dict[str, Any]:
    projection = current_reliability_projection(webspace_id=webspace_id)
    return {"ok": True, "projection": projection.to_dict()}


@router.get("/control-plane/projections/overview", dependencies=[Depends(require_token)])
async def node_control_plane_overview_projection(webspace_id: str | None = None, mode: str = "compact") -> dict[str, Any]:
    projection = current_overview_projection(webspace_id=webspace_id)
    token = str(mode or "compact").strip().lower()
    if token in {"compact", "thin"}:
        return {"ok": True, "mode": "compact", "projection": compact_overview_projection_dict(projection)}
    if token in {"full", "compat"}:
        return {"ok": True, "mode": "full", "projection": projection.to_dict()}
    raise HTTPException(status_code=400, detail="mode must be compact or full")


@router.get("/control-plane/projections/inventory", dependencies=[Depends(require_token)])
async def node_control_plane_inventory_projection() -> dict[str, Any]:
    projection = current_inventory_projection()
    return {"ok": True, "projection": projection.to_dict()}


@router.get("/control-plane/projections/neighborhood", dependencies=[Depends(require_token)])
async def node_control_plane_neighborhood_projection(object_id: str | None = None, webspace_id: str | None = None) -> dict[str, Any]:
    try:
        projection = current_neighborhood_projection(object_id=object_id, webspace_id=webspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown control-plane object: {exc.args[0]}") from exc
    return {"ok": True, "projection": projection.to_dict()}


@router.get("/control-plane/projections/object", dependencies=[Depends(require_token)])
async def node_control_plane_object_projection(object_id: str, webspace_id: str | None = None) -> dict[str, Any]:
    try:
        projection = current_object_projection(object_id, webspace_id=webspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown control-plane object: {exc.args[0]}") from exc
    return {"ok": True, "projection": projection.to_dict()}


@router.get("/control-plane/projections/object-inspector", dependencies=[Depends(require_token)])
async def node_control_plane_object_inspector(object_id: str, task_goal: str | None = None, webspace_id: str | None = None) -> dict[str, Any]:
    try:
        projection = current_object_inspector(object_id, task_goal=task_goal, webspace_id=webspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown control-plane object: {exc.args[0]}") from exc
    return {"ok": True, "projection": projection.to_dict()}


@router.get("/control-plane/projections/topology", dependencies=[Depends(require_token)])
async def node_control_plane_topology_projection(object_id: str, webspace_id: str | None = None) -> dict[str, Any]:
    try:
        projection = current_topology_projection(object_id, webspace_id=webspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown control-plane object: {exc.args[0]}") from exc
    return {"ok": True, "projection": projection.to_dict()}


@router.get("/control-plane/projections/task-packet", dependencies=[Depends(require_token)])
async def node_control_plane_task_packet(object_id: str, task_goal: str | None = None, webspace_id: str | None = None) -> dict[str, Any]:
    try:
        projection = current_task_packet(object_id, task_goal=task_goal, webspace_id=webspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown control-plane object: {exc.args[0]}") from exc
    return {"ok": True, "projection": projection.to_dict()}


@router.get("/control-plane/contexts/subnet-planning", dependencies=[Depends(require_token)])
async def node_control_plane_subnet_planning_context(
    object_id: str | None = None,
    task_goal: str | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    try:
        context = current_subnet_planning_context(
            object_id=object_id,
            task_goal=task_goal,
            webspace_id=webspace_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown control-plane object: {exc.args[0]}") from exc
    return {"ok": True, "context": context}


def _ensure_projection_runtime_handlers() -> None:
    ensure_status_card_projection_handler()


@router.get("/event-envelope-contract", dependencies=[Depends(require_token)])
async def node_event_envelope_contract() -> dict[str, Any]:
    return event_envelope_contract_snapshot(now=time.time())


@router.get("/projection-runtime-ownership", dependencies=[Depends(require_token)])
async def node_projection_runtime_ownership() -> dict[str, Any]:
    return projection_runtime_ownership_contract_snapshot(now=time.time())


@router.get("/projection-platform-emitters", dependencies=[Depends(require_token)])
async def node_projection_platform_emitters() -> dict[str, Any]:
    payload = platform_emitter_contract_snapshot(now=time.time())
    payload["event_bridge"] = projection_event_bridge_snapshot(now=time.time())
    return payload


@router.get("/projection-pilot/readiness-contract", dependencies=[Depends(require_token)])
async def node_projection_pilot_readiness_contract() -> dict[str, Any]:
    return projection_pilot_readiness_contract_snapshot(now=time.time())


@router.get("/projection-demand/contract", dependencies=[Depends(require_token)])
async def node_projection_demand_contract() -> dict[str, Any]:
    return client_subscription_contract_snapshot(now=time.time())


@router.get("/projection-demand/surface-lifecycle-contract", dependencies=[Depends(require_token)])
async def node_projection_demand_surface_lifecycle_contract() -> dict[str, Any]:
    return browser_surface_lifecycle_contract_snapshot(now=time.time())


@router.get("/projection-demand/restore-contract", dependencies=[Depends(require_token)])
async def node_projection_demand_restore_contract() -> dict[str, Any]:
    return projection_demand_restore_contract_snapshot(now=time.time())


@router.get("/projection-demand/yjs", dependencies=[Depends(require_token)])
async def node_projection_demand_yjs(webspace_id: str | None = None) -> dict[str, Any]:
    return await read_projection_demand_yjs(webspace_id=_coerce_node_webspace_id(webspace_id))


@router.post("/projection-demand/yjs/materialize", dependencies=[Depends(require_token)])
async def node_projection_demand_yjs_materialize(
    payload: ProjectionDemandYjsMaterializeRequest | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    request_payload = payload or ProjectionDemandYjsMaterializeRequest()
    return await materialize_projection_demand_to_yjs(
        webspace_id=_coerce_node_webspace_id(request_payload.webspace_id or webspace_id),
        include_stale=request_payload.include_stale,
        stale_after_s=resolve_projection_demand_stale_after_s(request_payload.stale_after_s),
        now=request_payload.now,
    )


@router.post("/projection-demand/yjs/restore", dependencies=[Depends(require_token)])
async def node_projection_demand_yjs_restore(
    payload: ProjectionDemandYjsRestoreRequest | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    request_payload = payload or ProjectionDemandYjsRestoreRequest()
    return await restore_projection_demand_from_yjs(
        webspace_id=_coerce_node_webspace_id(request_payload.webspace_id or webspace_id),
        include_hidden=request_payload.include_hidden,
        include_stale=request_payload.include_stale,
        stale_after_s=request_payload.stale_after_s,
        now=request_payload.now,
    )


@router.get("/projection-demand", dependencies=[Depends(require_token)])
async def node_projection_demand(
    webspace_id: str | None = None,
    include_stale: bool = True,
    stale_after_s: float | None = None,
) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    resolved_stale_after_s = resolve_projection_demand_stale_after_s(stale_after_s)
    return projection_demand_snapshot(
        webspace_id=target_webspace_id,
        include_stale=include_stale,
        stale_after_s=resolved_stale_after_s,
    )


@router.post("/projection-demand/client", dependencies=[Depends(require_token)])
async def node_projection_demand_client(payload: ClientProjectionDemandRequest) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(payload.webspace_id)
    try:
        record = write_client_subscription_record(
            {
                "client_id": payload.client_id,
                "device_id": payload.device_id,
                "session_id": payload.session_id,
                "webspace_id": target_webspace_id,
                "role": payload.role,
                "subscriptions": payload.subscriptions,
                "updated_at": payload.updated_at,
            }
        )
    except ValueError as exc:
        _raise_400(str(exc))
    yjs = await safe_materialize_projection_demand_to_yjs(webspace_id=target_webspace_id)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "record": record.to_dict(),
        "snapshot": projection_demand_snapshot(webspace_id=target_webspace_id),
        "yjs": yjs,
    }


@router.post("/projection-demand/browser-state", dependencies=[Depends(require_token)])
async def node_projection_demand_browser_state(payload: BrowserProjectionDemandStateRequest) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(payload.webspace_id)
    record = build_browser_projection_demand_record(
        client_id=payload.client_id,
        device_id=payload.device_id,
        session_id=payload.session_id,
        webspace_id=target_webspace_id,
        role=payload.role,
        page=payload.page,
        widgets=payload.widgets,
        modals=payload.modals,
        pinned_panels=payload.pinned_panels if payload.pinned_panels is not None else payload.pinnedPanels,
        updated_at=payload.updated_at,
    )
    stored = write_client_subscription_record(record)
    yjs = await safe_materialize_projection_demand_to_yjs(webspace_id=target_webspace_id)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "record": stored.to_dict(),
        "snapshot": projection_demand_snapshot(webspace_id=target_webspace_id),
        "yjs": yjs,
    }


@router.post("/projection-demand/client/{client_id}/{session_id}/touch", dependencies=[Depends(require_token)])
async def node_projection_demand_client_touch(
    client_id: str,
    session_id: str,
    payload: ClientProjectionDemandTouchRequest | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    request_payload = payload or ClientProjectionDemandTouchRequest()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    record = touch_client_subscription_record(
        client_id=client_id,
        session_id=session_id,
        webspace_id=target_webspace_id,
        device_id=request_payload.device_id,
        role=request_payload.role,
        updated_at=request_payload.updated_at,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="projection_demand_session_not_found")
    yjs = await safe_materialize_projection_demand_to_yjs(webspace_id=target_webspace_id)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "record": record.to_dict(),
        "snapshot": projection_demand_snapshot(webspace_id=target_webspace_id),
        "yjs": yjs,
    }


@router.delete("/projection-demand/client/{client_id}/{session_id}", dependencies=[Depends(require_token)])
async def node_projection_demand_client_delete(
    client_id: str,
    session_id: str,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    deleted = delete_client_subscription_record(
        client_id=client_id,
        session_id=session_id,
        webspace_id=target_webspace_id,
    )
    yjs = await safe_materialize_projection_demand_to_yjs(webspace_id=target_webspace_id)
    return {
        "ok": True,
        "accepted": deleted,
        "webspace_id": target_webspace_id,
        "deleted": deleted,
        "snapshot": projection_demand_snapshot(webspace_id=target_webspace_id),
        "yjs": yjs,
    }


@router.get("/projection-records", dependencies=[Depends(require_token)])
async def node_projection_records(webspace_id: str | None = None) -> dict[str, Any]:
    return projection_record_registry_snapshot(webspace_id=_coerce_node_webspace_id(webspace_id))


@router.get("/projection-records/item", dependencies=[Depends(require_token)])
async def node_projection_record_item(projection_key: str, webspace_id: str | None = None) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    record = get_projection_record(webspace_id=target_webspace_id, projection_key=projection_key)
    if record is None:
        raise HTTPException(status_code=404, detail="projection_record_not_found")
    return {"ok": True, "webspace_id": target_webspace_id, "record": record.to_dict()}


@router.get("/projection-records/browser-cache", dependencies=[Depends(require_token)])
async def node_projection_records_browser_cache(
    response: Response,
    webspace_id: str | None = None,
    client_id: str | None = None,
    session_id: str | None = None,
    projection_keys: list[str] | None = Query(default=None),
    include_hidden: bool = True,
    include_stale: bool = False,
    stale_after_s: float | None = None,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Any:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    payload = browser_projection_record_snapshot(
        webspace_id=target_webspace_id,
        client_id=client_id,
        session_id=session_id,
        projection_keys=projection_keys,
        include_hidden=include_hidden,
        include_stale=include_stale,
        stale_after_s=resolve_projection_demand_stale_after_s(stale_after_s),
    )
    etag = str(payload.get("etag") or "")
    headers = {"Cache-Control": "no-cache", "ETag": etag}
    response.headers.update(headers)
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)
    return payload


@router.get("/projection-records/browser-adapter-contract", dependencies=[Depends(require_token)])
async def node_projection_records_browser_adapter_contract() -> dict[str, Any]:
    return browser_projection_adapter_contract_snapshot(now=time.time())


@router.get("/projection-records/node-multiplicity-contract", dependencies=[Depends(require_token)])
async def node_projection_records_node_multiplicity_contract() -> dict[str, Any]:
    return projection_records_node_multiplicity_contract_snapshot(now=time.time())


@router.get("/platform/nodes/contract", dependencies=[Depends(require_token)])
async def node_platform_nodes_contract() -> dict[str, Any]:
    return platform_nodes_contract_snapshot(now=time.time())


@router.get("/platform/nodes/yjs", dependencies=[Depends(require_token)])
async def node_platform_nodes_yjs(webspace_id: str | None = None) -> dict[str, Any]:
    return await read_platform_nodes_yjs(webspace_id=_coerce_node_webspace_id(webspace_id))


@router.post("/platform/nodes/materialize", dependencies=[Depends(require_token)])
async def node_platform_nodes_materialize(
    payload: PlatformNodeYjsMaterializeRequest | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    request_payload = payload or PlatformNodeYjsMaterializeRequest()
    return await materialize_platform_node_to_yjs(
        webspace_id=_coerce_node_webspace_id(request_payload.webspace_id or webspace_id),
        node_id=request_payload.node_id,
        status=request_payload.status,
        diagnostics=request_payload.diagnostics,
        projections=request_payload.projections,
        now=request_payload.now,
    )


@router.get("/projection-records/yjs/cache", dependencies=[Depends(require_token)])
async def node_projection_records_yjs_cache(webspace_id: str | None = None) -> dict[str, Any]:
    return await read_projection_records_yjs_cache(webspace_id=_coerce_node_webspace_id(webspace_id))


@router.post("/projection-records/yjs/materialize", dependencies=[Depends(require_token)])
async def node_projection_records_yjs_materialize(
    payload: ProjectionRecordsYjsMaterializeRequest | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    request_payload = payload or ProjectionRecordsYjsMaterializeRequest()
    return await materialize_projection_records_to_yjs(
        webspace_id=_coerce_node_webspace_id(request_payload.webspace_id or webspace_id),
        projection_keys=request_payload.projection_keys,
        demanded_only=request_payload.demanded_only,
        now=request_payload.now,
    )


@router.post("/projection-records/status-cards/materialize", dependencies=[Depends(require_token)])
async def node_projection_records_materialize_status_cards(
    payload: StatusCardProjectionRecordsMaterializeRequest | None = None,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    request_payload = payload or StatusCardProjectionRecordsMaterializeRequest()
    target_webspace_id = _coerce_node_webspace_id(request_payload.webspace_id or webspace_id)
    result = materialize_status_card_projection_records(
        webspace_id=target_webspace_id,
        card_ids=request_payload.card_ids,
        demanded_only=request_payload.demanded_only,
        now=request_payload.now,
        access={"audience": "shared", "visibility": "operator"},
    )
    if request_payload.write_yjs:
        result["yjs"] = await materialize_projection_records_to_yjs(
            webspace_id=target_webspace_id,
            projection_keys=normalize_projection_record_keys(result.get("records") or []),
            demanded_only=False,
            now=request_payload.now,
        )
    return result


@router.get("/projection-dispatcher", dependencies=[Depends(require_token)])
async def node_projection_dispatcher_snapshot() -> dict[str, Any]:
    _ensure_projection_runtime_handlers()
    return projection_dispatcher_snapshot()


@router.get("/projection-dispatcher/core-skill-contract", dependencies=[Depends(require_token)])
async def node_projection_dispatcher_core_skill_contract(
    webspace_id: str | None = None,
    projection_keys: list[str] | None = Query(default=None),
    include_hidden: bool = True,
    include_stale: bool = False,
    stale_after_s: float | None = None,
) -> dict[str, Any]:
    _ensure_projection_runtime_handlers()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    return core_skill_refresh_contract_snapshot(
        webspace_ids=[target_webspace_id],
        projection_keys=projection_keys,
        include_hidden=include_hidden,
        include_stale=include_stale,
        stale_after_s=resolve_projection_demand_stale_after_s(stale_after_s),
    )


@router.get("/projection-dispatcher/memory-contract", dependencies=[Depends(require_token)])
async def node_projection_dispatcher_memory_contract() -> dict[str, Any]:
    return projection_dispatcher_memory_contract_snapshot(now=time.time())


@router.post("/projection-dispatcher/dispatch", dependencies=[Depends(require_token)])
async def node_projection_dispatcher_dispatch(payload: ProjectionDispatchRequest) -> dict[str, Any]:
    _ensure_projection_runtime_handlers()
    event = Event(
        type=payload.type,
        payload=payload.payload,
        source=payload.source,
        ts=float(payload.ts if payload.ts is not None else time.time()),
    )
    report = await dispatch_demanded_projection_refresh(
        event,
        webspace_ids=payload.webspace_ids,
        projection_keys=payload.projection_keys,
    )
    return {
        "ok": True,
        "accepted": True,
        "report": report.to_dict(),
        "dispatcher": projection_dispatcher_snapshot(),
    }


@router.get("/projection-diagnostics", dependencies=[Depends(require_token)])
async def node_projection_diagnostics(
    webspace_id: str | None = None,
    include_stale: bool = False,
    stale_after_s: float | None = None,
    include_yjs_cache: bool = False,
    materialize_yjs_cache: bool = False,
    materialize_status_cards: bool = False,
) -> dict[str, Any]:
    _ensure_projection_runtime_handlers()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    refreshes: dict[str, Any] = {}
    if materialize_status_cards:
        refreshes["status_cards"] = materialize_status_card_projection_records(
            webspace_id=target_webspace_id,
            demanded_only=True,
            access={"audience": "shared", "visibility": "operator"},
        )
    if materialize_yjs_cache:
        refreshes["projection_records_yjs"] = await materialize_projection_records_to_yjs(
            webspace_id=target_webspace_id,
            demanded_only=True,
        )
    yjs_cache = None
    if include_yjs_cache or materialize_yjs_cache:
        yjs_cache = await read_projection_records_yjs_cache(webspace_id=target_webspace_id)
    diagnostics = projection_operator_diagnostics(
        webspace_id=target_webspace_id,
        include_stale=include_stale,
        stale_after_s=resolve_projection_demand_stale_after_s(stale_after_s),
        yjs_cache=yjs_cache,
    )
    if refreshes:
        diagnostics["refreshes"] = refreshes
    return diagnostics


@router.get("/reliability", dependencies=[Depends(require_token)])
async def node_reliability() -> Response:
    started_at = time.time()
    try:
        payload = await _current_reliability_payload_async()
        body = await asyncio.to_thread(_json_response_body, payload)
        body_bytes = len(body)
        duration_ms = max(0.0, (time.time() - started_at) * 1000.0)
        _record_runtime_endpoint_metric(
            endpoint="/api/node/reliability",
            duration_ms=duration_ms,
            status_code=200,
            body_bytes=body_bytes,
        )
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "X-AdaOS-Runtime-Duration-Ms": str(round(duration_ms, 3)),
                "X-AdaOS-Runtime-Body-Bytes": str(body_bytes),
            },
        )
    except Exception as exc:
        duration_ms = max(0.0, (time.time() - started_at) * 1000.0)
        _record_runtime_endpoint_metric(
            endpoint="/api/node/reliability",
            duration_ms=duration_ms,
            status_code=500,
            body_bytes=0,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


@router.get("/reliability/update-gate", dependencies=[Depends(require_token)])
async def node_reliability_update_gate() -> dict[str, Any]:
    migration = await asyncio.to_thread(skill_runtime_migration_update_gate_snapshot)
    return {
        "ok": True,
        "schema": "adaos.reliability_update_gate.v1",
        "captured_at": time.time(),
        "runtime": {
            "skill_runtime_migration": migration,
        },
    }


@router.get("/reliability/supervisor-channel", dependencies=[Depends(require_token)])
async def node_reliability_supervisor_channel() -> dict[str, Any]:
    return await asyncio.to_thread(_node_reliability_supervisor_channel_payload)


def _runtime_node_config() -> Any:
    try:
        conf = getattr(get_ctx(), "config", None)
    except Exception:
        conf = None
    return conf if conf is not None else load_config()


def _node_reliability_supervisor_channel_payload() -> dict[str, Any]:
    conf = _runtime_node_config()
    route_mode, connected = route_info(conf.role)
    lifecycle = runtime_lifecycle_snapshot()
    runtime = supervisor_channel_runtime_snapshot(
        node_id=conf.node_id,
        role=conf.role,
        local_ready=is_ready(),
        node_state=str(lifecycle.get("node_state") or "ready"),
        draining=bool(lifecycle.get("draining")),
        route_mode=route_mode,
        connected_to_hub=connected,
        node_names=list(getattr(conf, "node_names", []) or []),
    )
    return {
        "ok": True,
        "schema": "adaos.reliability_supervisor_channel.v1",
        "captured_at": time.time(),
        "runtime": runtime,
    }


@router.get("/reliability/runtime", dependencies=[Depends(require_token)])
async def node_reliability_runtime(
    webspace_id: str | None = None,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    started_at = time.time()
    return await run_reliability_runtime_beacon(
        _thin_runtime_reliability_response,
        timeout_fallback=_runtime_beacon_unavailable_response,
        webspace_id=webspace_id,
        mode="runtime",
        if_none_match=if_none_match,
        started_at=started_at,
        endpoint="/api/node/reliability/runtime",
        include_status_registry=False,
    )


@router.get("/reliability/details", dependencies=[Depends(require_token)])
async def node_reliability_details(
    webspace_id: str | None = None,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    started_at = time.time()
    return await asyncio.to_thread(
        _thin_runtime_reliability_response,
        webspace_id=webspace_id,
        mode="details",
        if_none_match=if_none_match,
        started_at=started_at,
        endpoint="/api/node/reliability/details",
        include_status_registry=True,
    )


@router.get("/reliability/summary", dependencies=[Depends(require_token)])
async def node_reliability_summary(
    webspace_id: str | None = None,
    mode: str | None = None,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
) -> Response:
    started_at = time.time()
    requested_mode = str(mode or "compat").strip().lower()
    if requested_mode in {"runtime", "beacon"}:
        return await run_reliability_runtime_beacon(
            _thin_runtime_reliability_response,
            timeout_fallback=_runtime_beacon_unavailable_response,
            webspace_id=webspace_id,
            mode="runtime",
            if_none_match=if_none_match,
            started_at=started_at,
            include_status_registry=False,
        )

    if requested_mode in {"details", "detail"}:
        return await asyncio.to_thread(
            _thin_runtime_reliability_response,
            webspace_id=webspace_id,
            mode="details",
            if_none_match=if_none_match,
            started_at=started_at,
            include_status_registry=True,
        )

    if requested_mode in {"thin", "status", "status_plane"}:
        return await asyncio.to_thread(
            _thin_runtime_reliability_response,
            webspace_id=webspace_id,
            mode="thin",
            if_none_match=if_none_match,
            started_at=started_at,
            include_status_registry=True,
        )

    reliability = await _current_reliability_payload_async(webspace_id=webspace_id)
    payload = await asyncio.to_thread(
        _compact_reliability_summary_payload,
        reliability,
        webspace_id=webspace_id,
        mode="full" if requested_mode == "full" else "compat",
    )
    return await asyncio.to_thread(
        _json_response_with_etag,
        payload,
        if_none_match=if_none_match,
        mode=str(payload["mode"]),
        started_at=started_at,
    )


@router.get("/reliability/summary/metrics", dependencies=[Depends(require_token)])
async def node_reliability_summary_metrics(
    webspace_id: str | None = None,
    receiver: str | None = None,
    owner: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    return {
        "ok": True,
        "metrics": _reliability_summary_metrics_snapshot(
            webspace_id=webspace_id,
            receiver=receiver,
            owner=owner,
            limit=limit,
        ),
    }


@router.get("/status/cards", dependencies=[Depends(require_token)])
async def node_status_cards(
    webspace_id: str | None = None,
    owner: str | None = None,
    scope: str | None = None,
    include_stale: bool = True,
    limit: int = 100,
) -> dict[str, Any]:
    snapshot = _current_status_registry_snapshot(
        webspace_id=webspace_id,
        owner=owner,
        scope=scope,
        include_stale=include_stale,
    )
    return _compact_status_registry_payload(
        snapshot,
        webspace_id=webspace_id,
        limit=limit,
        source="api.node.status.cards",
    )


@router.post("/hub-root/reconnect", dependencies=[Depends(require_token)])
async def hub_root_reconnect(payload: HubRootReconnectRequest) -> dict[str, Any]:
    return await request_hub_root_reconnect(transport=payload.transport, url_override=payload.url_override)


@router.post("/member-hub/reconnect", dependencies=[Depends(require_token)])
async def member_hub_reconnect(payload: MemberHubReconnectRequest) -> dict[str, Any]:
    return await request_member_hub_reconnect(force=bool(payload.force))


@router.post("/member-hub/refresh", dependencies=[Depends(require_token)])
async def member_hub_refresh(payload: MemberHubRefreshRequest) -> dict[str, Any]:
    return await request_member_hub_refresh(reason=str(payload.reason or "member_hub_refresh"))


@router.post("/hub-root/route-reset", dependencies=[Depends(require_token)])
async def hub_root_route_reset(payload: HubRootRouteResetRequest) -> dict[str, Any]:
    return await request_hub_root_route_reset(
        reason=str(payload.reason or "").strip() or "supervisor_route_watchdog",
        notify_browser=bool(payload.notify_browser),
    )


@router.get("/sidecar/status", dependencies=[Depends(require_token)])
async def sidecar_status(request: Request) -> dict[str, Any]:
    started_at = time.time()
    if _supervisor_enabled():
        payload = await _proxy_supervisor_json(method="GET", path="/api/supervisor/sidecar/status", timeout=3.0)
        duration_ms = max(0.0, (time.time() - started_at) * 1000.0)
        _record_runtime_endpoint_metric(
            endpoint="/api/node/sidecar/status",
            duration_ms=duration_ms,
            status_code=200,
            body_bytes=_summary_body_size(payload),
        )
        return payload
    try:
        conf = await anyio.to_thread.run_sync(load_config)
        runtime = sidecar_runtime_snapshot(role=conf.role)
        process = realtime_sidecar_listener_snapshot(
            getattr(request.app.state, "realtime_sidecar_proc", None),
            role=conf.role,
        )
        payload = {
            "ok": True,
            "runtime": runtime,
            "process": process,
        }
        duration_ms = max(0.0, (time.time() - started_at) * 1000.0)
        _record_runtime_endpoint_metric(
            endpoint="/api/node/sidecar/status",
            duration_ms=duration_ms,
            status_code=200,
            body_bytes=_summary_body_size(payload),
        )
        return payload
    except Exception as exc:
        duration_ms = max(0.0, (time.time() - started_at) * 1000.0)
        _record_runtime_endpoint_metric(
            endpoint="/api/node/sidecar/status",
            duration_ms=duration_ms,
            status_code=500,
            body_bytes=0,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise


@router.post("/sidecar/restart", dependencies=[Depends(require_token)])
async def sidecar_restart(request: Request, payload: SidecarRestartRequest) -> dict[str, Any]:
    if _supervisor_enabled():
        return await _proxy_supervisor_json(
            method="POST",
            path="/api/supervisor/sidecar/restart",
            payload={
                "reconnect_hub_root": bool(payload.reconnect_hub_root),
                "allow_active_channel_disruption": bool(payload.allow_active_channel_disruption),
            },
            timeout=10.0,
        )
    conf = await anyio.to_thread.run_sync(load_config)
    if str(conf.role or "").strip().lower() == "hub" and not payload.allow_active_channel_disruption:
        reliability_before = await _current_reliability_payload_async()
        runtime_before = (
            reliability_before.get("runtime")
            if isinstance(reliability_before.get("runtime"), dict)
            else {}
        )
        sidecar_before = (
            runtime_before.get("sidecar_runtime")
            if isinstance(runtime_before.get("sidecar_runtime"), dict)
            else {}
        )
        remote_state = str(sidecar_before.get("remote_session_state") or "").strip().lower()
        sidecar_status = str(sidecar_before.get("status") or "").strip().lower()
        if bool(
            sidecar_before.get("active_session")
            or sidecar_before.get("transport_ready")
            or remote_state == "ready"
            or sidecar_status == "ready"
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "active_sidecar_channel",
                    "message": "sidecar restart would disrupt active NATS and browser proxy sessions",
                    "required_override": "allow_active_channel_disruption=true",
                },
            )
    proc = getattr(request.app.state, "realtime_sidecar_proc", None)
    new_proc, restart_result = await restart_realtime_sidecar_subprocess(proc=proc, role=conf.role)
    request.app.state.realtime_sidecar_proc = new_proc
    reconnect_result: dict[str, Any] | None = None
    if bool(payload.reconnect_hub_root) and str(conf.role or "").strip().lower() == "hub":
        reconnect_result = await request_hub_root_reconnect()
    reliability = await _current_reliability_payload_async()
    runtime = reliability.get("runtime") if isinstance(reliability.get("runtime"), dict) else {}
    return {
        "ok": True,
        "restart": restart_result,
        "reconnect": reconnect_result,
        "runtime": runtime.get("sidecar_runtime") if isinstance(runtime.get("sidecar_runtime"), dict) else {},
        "process": realtime_sidecar_listener_snapshot(new_proc, role=conf.role),
    }


@router.post("/role", response_model=RoleChangeResponse, dependencies=[Depends(require_token)])
async def node_change_role(req: Request, payload: RoleChangeRequest):
    """
    Switch local node role.

    Backward-compatibility: `hub_url` is accepted but ignored (deprecated).
    """
    new_role = payload.role.lower().strip()
    sub_id = payload.subnet_id
    deprecated_fields: list[str] = ["hub_url"] if payload.hub_url else []

    conf = await switch_role(req.app, new_role, hub_url=None, subnet_id=sub_id)
    route_mode, connected = route_info(conf.role)
    display = _local_node_display()

    diags = {
        "requested_role": new_role,
        "subnet_id_used": sub_id,
        "now_ready": is_ready(),
        "node_state": runtime_lifecycle_snapshot().get("node_state", "ready"),
        "route_mode": route_mode,
        "connected_to_subnet": connected,
        "connected_to_hub": connected,
        "deprecated_fields": deprecated_fields,
    }
    return RoleChangeResponse(
        ok=True,
        node=NodeStatus(
            node_id=conf.node_id,
            subnet_id=conf.subnet_id,
            role=conf.role,
            node_names=list(getattr(conf, "node_names", []) or []),
            primary_node_name=str(getattr(conf, "primary_node_name", "") or ""),
            node_label=str(display.get("node_label") or ""),
            node_compact_label=str(display.get("node_compact_label") or ""),
            node_index=display.get("node_index"),
            node_color=display.get("node_color"),
            ready=is_ready(),
            node_state=str(runtime_lifecycle_snapshot().get("node_state") or "ready"),
            draining=bool(runtime_lifecycle_snapshot().get("draining")),
            route_mode=route_mode,
            connected_to_subnet=connected,
            connected_to_hub=connected,
        ),
        diagnostics=diags,
    )


@router.get("/names", dependencies=[Depends(require_token)])
async def node_names() -> dict[str, Any]:
    conf = load_config()
    display = _local_node_display()
    return {
        "ok": True,
        "node_id": conf.node_id,
        "role": conf.role,
        "node_names": list(getattr(conf, "node_names", []) or []),
        "primary_node_name": str(getattr(conf, "primary_node_name", "") or ""),
        "node_label": display.get("node_label"),
        "node_compact_label": display.get("node_compact_label"),
        "node_index": display.get("node_index"),
        "node_color": display.get("node_color"),
    }


@router.post("/names", dependencies=[Depends(require_token)])
async def update_node_names(payload: NodeNamesUpdateRequest) -> dict[str, Any]:
    source = payload.node_names if payload.node_names is not None else payload.value
    conf = save_node_names_config(source)
    display = _local_node_display()
    return {
        "ok": True,
        "node_id": conf.node_id,
        "role": conf.role,
        "node_names": list(getattr(conf, "node_names", []) or []),
        "primary_node_name": str(getattr(conf, "primary_node_name", "") or ""),
        "node_label": display.get("node_label"),
        "node_compact_label": display.get("node_compact_label"),
        "node_index": display.get("node_index"),
        "node_color": display.get("node_color"),
    }


@router.get("/yjs/runtime", dependencies=[Depends(require_token)])
async def node_yjs_runtime(webspace_id: str | None = None) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    return {
        "ok": True,
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.get("/memory/status", dependencies=[Depends(require_token)])
async def node_memory_status() -> dict[str, Any]:
    """Return a cheap runtime-local memory snapshot.

    This endpoint intentionally does not depend on the supervisor memory bridge:
    when route/profiler plumbing is degraded, operators still need a bounded
    process RSS signal through the active runtime API.
    """
    pid = os.getpid()
    now = time.time()
    process: dict[str, Any] = {
        "pid": pid,
        "rss_bytes": None,
        "vms_bytes": None,
        "create_time": None,
        "uptime_s": None,
        "num_threads": None,
        "children_total": 0,
        "children_rss_bytes": 0,
        "family_rss_bytes": None,
    }
    psutil_error = ""
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        mem = proc.memory_info()
        rss = int(getattr(mem, "rss", 0) or 0)
        vms = int(getattr(mem, "vms", 0) or 0)
        create_time = float(proc.create_time())
        children = proc.children(recursive=True)
        children_rss = 0
        for child in children:
            try:
                children_rss += int(child.memory_info().rss)
            except Exception:
                continue
        process.update(
            {
                "rss_bytes": rss,
                "vms_bytes": vms,
                "create_time": create_time,
                "uptime_s": round(max(0.0, now - create_time), 3),
                "num_threads": int(proc.num_threads()),
                "children_total": len(children),
                "children_rss_bytes": children_rss,
                "family_rss_bytes": rss + children_rss,
            }
        )
    except Exception as exc:
        psutil_error = f"{type(exc).__name__}: {exc}"

    tracing = bool(tracemalloc.is_tracing())
    traced_current = None
    traced_peak = None
    if tracing:
        try:
            traced_current, traced_peak = tracemalloc.get_traced_memory()
        except Exception:
            traced_current = None
            traced_peak = None

    return {
        "ok": True,
        "ts": now,
        "node": _local_node_display(),
        "process": process,
        "python": {
            "gc_count": list(gc.get_count()),
            "gc_threshold": list(gc.get_threshold()),
            "tracemalloc_tracing": tracing,
            "tracemalloc_current_bytes": traced_current,
            "tracemalloc_peak_bytes": traced_peak,
        },
        "errors": {"psutil": psutil_error} if psutil_error else {},
    }


def _memory_loaded_module_flags() -> dict[str, bool]:
    modules = (
        "torch",
        "torchvision",
        "faiss",
        "numpy",
        "PIL",
        "cv2",
        "vosk",
        "av",
        "y_py",
        "sentence_transformers",
    )
    loaded = set(sys.modules)
    return {name: any(mod == name or mod.startswith(f"{name}.") for mod in loaded) for name in modules}


def _memory_top_gc_types(*, limit: int = 25) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for obj in gc.get_objects():
        typ = type(obj)
        module = getattr(typ, "__module__", "") or ""
        name = getattr(typ, "__qualname__", getattr(typ, "__name__", "")) or ""
        counts[f"{module}.{name}" if module else name] += 1
    return [{"type": key, "count": int(value)} for key, value in counts.most_common(max(1, int(limit)))]


def _windows_virtual_memory_summary() -> dict[str, Any]:
    if os.name != "nt":
        return {"available": False, "reason": "non_windows"}
    try:
        import ctypes
        from ctypes import wintypes

        MEM_COMMIT = 0x1000
        MEM_RESERVE = 0x2000
        MEM_PRIVATE = 0x20000
        MEM_MAPPED = 0x40000
        MEM_IMAGE = 0x1000000
        PAGE_NOACCESS = 0x01
        PAGE_READONLY = 0x02
        PAGE_READWRITE = 0x04
        PAGE_EXECUTE_READ = 0x20
        PAGE_EXECUTE_READWRITE = 0x40

        class MEMORY_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD),
                ("__alignment1", wintypes.WORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("__alignment2", wintypes.WORD),
            ]

        virtual_query = ctypes.windll.kernel32.VirtualQuery
        mbi = MEMORY_BASIC_INFORMATION()
        addr = 0
        summary: Counter[tuple[str, str, str]] = Counter()
        private_rw_sizes: Counter[int] = Counter()
        private_rw_total = 0
        private_commit_total = 0
        private_rw_regions = 0

        while addr < (1 << 47):
            ret = virtual_query(ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not ret:
                break
            size = int(mbi.RegionSize)
            state = int(mbi.State)
            typ = int(mbi.Type)
            protect = int(mbi.Protect)
            state_name = "COMMIT" if state == MEM_COMMIT else "RESERVE" if state == MEM_RESERVE else hex(state)
            type_name = (
                "PRIVATE"
                if typ == MEM_PRIVATE
                else "MAPPED"
                if typ == MEM_MAPPED
                else "IMAGE"
                if typ == MEM_IMAGE
                else hex(typ)
            )
            if protect & PAGE_READWRITE:
                protect_name = "RW"
            elif protect & PAGE_EXECUTE_READWRITE:
                protect_name = "XRW"
            elif protect & PAGE_READONLY:
                protect_name = "R"
            elif protect & PAGE_EXECUTE_READ:
                protect_name = "XR"
            elif protect & PAGE_NOACCESS:
                protect_name = "NOACCESS"
            else:
                protect_name = hex(protect)
            summary[(state_name, type_name, protect_name)] += size
            if state == MEM_COMMIT and typ == MEM_PRIVATE:
                private_commit_total += size
                if protect & PAGE_READWRITE:
                    private_rw_total += size
                    private_rw_regions += 1
                    private_rw_sizes[size] += 1
            addr = int(mbi.BaseAddress or addr) + size

        return {
            "available": True,
            "private_commit_bytes": int(private_commit_total),
            "private_rw_bytes": int(private_rw_total),
            "private_rw_regions": int(private_rw_regions),
            "top_regions": [
                {"state": key[0], "type": key[1], "protect": key[2], "bytes": int(value)}
                for key, value in summary.most_common(12)
            ],
            "top_private_rw_region_sizes": [
                {"bytes": int(size), "count": int(count), "total_bytes": int(size * count)}
                for size, count in private_rw_sizes.most_common(12)
            ],
        }
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


@router.get("/memory/diagnostics", dependencies=[Depends(require_token)])
async def node_memory_diagnostics(force_gc: bool = Query(False)) -> dict[str, Any]:
    """Return bounded heap/native-memory diagnostics for debug builds."""
    forced_gc: dict[str, Any] | None = None
    if force_gc:
        started = time.perf_counter()
        allow_unsafe_gc = str(os.getenv("ADAOS_MEMORY_DIAGNOSTICS_FORCE_GC_ALLOW_UNSAFE") or "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if "y_py" in sys.modules and not allow_unsafe_gc:
            forced_gc = {
                "requested": True,
                "skipped": "unsafe:y_py_loaded",
                "allow_env": "ADAOS_MEMORY_DIAGNOSTICS_FORCE_GC_ALLOW_UNSAFE",
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            }
        else:
            try:
                collected = int(gc.collect() or 0)
                forced_gc = {
                    "requested": True,
                    "collected": collected,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                }
            except BaseException as exc:
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                forced_gc = {
                    "requested": True,
                    "error": f"{type(exc).__name__}: {exc}",
                    "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                }
    base = await node_memory_status()
    errors: dict[str, str] = dict(base.get("errors") or {})
    gc_types: list[dict[str, Any]] = []
    try:
        gc_types = _memory_top_gc_types()
    except Exception as exc:
        errors["gc_types"] = f"{type(exc).__name__}: {exc}"
    return {
        **base,
        "diagnostics": {
            "forced_gc": forced_gc or {"requested": False},
            "loaded_modules": _memory_loaded_module_flags(),
            "allocated_blocks": int(getattr(sys, "getallocatedblocks", lambda: 0)()),
            "gc_objects_total": len(gc.get_objects()),
            "top_gc_types": gc_types,
            "virtual_memory": _windows_virtual_memory_summary(),
        },
        "errors": errors,
    }


@router.get("/infrastate/snapshot", dependencies=[Depends(require_token)])
async def node_infrastate_snapshot(webspace_id: str | None = None) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    lifecycle = runtime_lifecycle_snapshot()
    yjs_runtime = yjs_sync_runtime_snapshot(
        role=str(getattr(conf, "role", "") or ""),
        webspace_id=target_webspace_id,
    )
    try:
        from adaos.services.yjs.gateway import yjs_balancer_snapshot

        yjs_balancer = yjs_balancer_snapshot(webspace_id=target_webspace_id)
    except Exception as exc:
        yjs_balancer = {
            "schema": "adaos.yjs_balancer.v1",
            "webspace_id": target_webspace_id,
            "updated_at": time.time(),
            "state": "unavailable",
            "reason": f"{type(exc).__name__}: {exc}",
            "health": {"available": False},
            "limits": {},
            "usage": {},
            "guard": {},
            "observed": {},
        }
    snapshot = {
        "summary": {
            "label": "Infra State",
            "value": str(lifecycle.get("node_state") or "ready"),
            "subtitle": f"webspace {target_webspace_id}",
            "description": "Full Infra State snapshot is disabled; use YJS control projection and webio streams.",
            "updated_at": time.time(),
        },
        "lifecycle": lifecycle,
        "yjs_runtime": yjs_runtime,
        "yjs_balancer": yjs_balancer,
        "last_refresh_ts": time.time(),
        "full_snapshot_removed": True,
        "projection": "lightweight_control",
        "details": {"delivery": "streams"},
    }
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "degraded": False,
        "error": None,
        "snapshot": snapshot,
    }


@router.get("/logs/{category}", dependencies=[Depends(require_token)])
async def node_logs(
    category: str,
    limit: int = 5,
    lines: int = 200,
    contains: str | None = None,
    skill: str | None = None,
    file: str | None = None,
) -> dict[str, Any]:
    try:
        category_token = normalize_log_category(category)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown log category: {category}") from exc

    def _load_logs() -> dict[str, Any]:
        return list_local_logs(
            category=category_token,
            limit=limit,
            lines=lines,
            contains=contains,
            skill=skill,
            file=file,
            source_mode="node_local_logs_dir",
        )

    return {"ok": True, "logs": await anyio.to_thread.run_sync(_load_logs)}


async def _read_webui_contract_diagnostics(webspace_id: str) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    try:
        async with async_read_ydoc(target_webspace_id, prefer_live_room=True) as ydoc:
            ui_map = ydoc.get_map("ui")
            application = _coerce_dict(_clone_json_like(ui_map.get("application")))
    except Exception as exc:
        return {
            "ok": False,
            "schema": "adaos.webui.contract_diagnostics.v1",
            "webspace_id": target_webspace_id,
            "status": "unavailable",
            "source": "ui.application.diagnostics.webui_contract",
            "materialized": False,
            "error": f"{type(exc).__name__}: {exc}",
            "issues": [],
            "error_count": 0,
            "warning_count": 0,
        }
    diagnostics = _coerce_dict(_coerce_dict(application.get("diagnostics")).get("webui_contract"))
    raw_issues = _coerce_list(diagnostics.get("issues"))
    issues = [_coerce_dict(item) for item in raw_issues if isinstance(item, dict)]
    error_count = int(diagnostics.get("error_count") or sum(1 for item in issues if item.get("level") == "error"))
    warning_count = int(diagnostics.get("warning_count") or sum(1 for item in issues if item.get("level") == "warning"))
    status = str(diagnostics.get("status") or ("valid" if diagnostics else "missing")).strip() or "missing"
    return {
        "ok": True,
        "schema": "adaos.webui.contract_diagnostics.v1",
        "webspace_id": target_webspace_id,
        "status": status,
        "source": "ui.application.diagnostics.webui_contract",
        "materialized": bool(diagnostics),
        "error_count": error_count,
        "warning_count": warning_count,
        "issue_count": len(issues),
        "issues": issues,
        "summary": {
            "status": status,
            "error_count": error_count,
            "warning_count": warning_count,
            "issue_count": len(issues),
        },
    }


@router.get("/ui/contract-diagnostics", dependencies=[Depends(require_token)])
async def node_ui_contract_diagnostics(
    webspace_id: str | None = None,
    include_catalog: bool = True,
) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    payload = await _read_webui_contract_diagnostics(target_webspace_id)
    if include_catalog:
        payload["catalog"] = webui_contract_diagnostic_catalog()
    return payload


@router.post("/ui/diagnostics", dependencies=[Depends(require_token)])
async def node_ui_runtime_diagnostics(payload: UiRuntimeDiagnosticsRequest) -> dict[str, Any]:
    return await ingest_ui_runtime_diagnostics(
        {"webspace_id": payload.webspace_id, "events": payload.events},
        webspace_id=payload.webspace_id,
    )


@router.post("/events/publish", dependencies=[Depends(require_token)])
async def node_skill_event_publish(payload: SkillEventPublishRequest) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(payload.webspace_id or payload.workspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    event_type = str(payload.event_type or payload.type or "").strip()
    if not event_type:
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "event_type required",
        }
    raw_event_payload = payload.payload
    if isinstance(raw_event_payload, dict):
        event_payload: dict[str, Any] = dict(raw_event_payload)
    elif raw_event_payload is None:
        event_payload = {}
    else:
        event_payload = {"value": raw_event_payload}
    event_payload.setdefault("webspace_id", target_webspace_id)
    for key in ("workspace_id", "node_id", "target_node_id"):
        value = getattr(payload, key, None)
        if value is not None and not event_payload.get(key):
            event_payload[key] = value
    meta = dict(event_payload.get("_meta") or {})
    if isinstance(payload.meta, dict):
        for key, value in payload.meta.items():
            meta.setdefault(key, value)
    meta.setdefault("webspace_id", target_webspace_id)
    target_node_id = str(
        event_payload.get("target_node_id")
        or event_payload.get("node_target_id")
        or meta.get("target_node_id")
        or meta.get("node_target_id")
        or event_payload.get("node_id")
        or ""
    ).strip()
    if target_node_id:
        event_payload.setdefault("target_node_id", target_node_id)
        meta.setdefault("target_node_id", target_node_id)
    event_payload["_meta"] = meta
    ctx = get_ctx()
    ctx.bus.publish(Event(type=event_type, payload=event_payload, source="api.node", ts=time.time()))
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "event_type": event_type,
    }


@router.post("/infrastate/action", dependencies=[Depends(require_token)])
async def node_infrastate_action(payload: InfrastateActionRequest) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(payload.webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    ctx = get_ctx()
    action_id = str(payload.id or "").strip()
    if action_id == "marketplace_install":
        action_payload = payload.model_dump(exclude_none=True)
        try:
            operation = submit_marketplace_install_action(
                action_payload,
                webspace_id=target_webspace_id,
                initiator_kind="api.node",
                ctx=ctx,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "accepted": False,
                "webspace_id": target_webspace_id,
                "action": action_id,
                "error": str(exc) or "marketplace_install_invalid",
            }
        value = payload.value if isinstance(payload.value, dict) else {}
        target_node_id = str(value.get("target_node_id") or value.get("node_id") or payload.target_node_id or payload.node_id or "").strip()
        return {
            "ok": True,
            "accepted": True,
            "webspace_id": target_webspace_id,
            "action": action_id,
            "target_node_id": target_node_id or None,
            "operation_id": operation.get("operation_id"),
            "result": {
                "ok": True,
                "accepted": True,
                "operation_id": operation.get("operation_id"),
                "operation": operation,
            },
            "snapshot": {},
        }
    event_payload: dict[str, Any] = {
        "id": action_id,
        "webspace_id": target_webspace_id,
    }
    name = str(payload.name or "").strip()
    request_id = str(payload.request_id or "").strip()
    node_id = str(payload.node_id or payload.target_node_id or "").strip()
    target_node_id = str(payload.target_node_id or payload.node_id or "").strip()
    value = payload.value
    if name:
        event_payload["name"] = name
    if request_id:
        event_payload["request_id"] = request_id
    if node_id:
        event_payload["node_id"] = node_id
    if target_node_id:
        event_payload["target_node_id"] = target_node_id
    if value is not None:
        event_payload["value"] = value
    ctx.bus.publish(Event(type="infrastate.action", payload=event_payload, source="api.node", ts=time.time()))
    waiter = getattr(ctx.bus, "wait_for_idle", None)
    if callable(waiter):
        try:
            await waiter(timeout=2.5)
        except Exception:
            _log.debug("wait_for_idle failed after infrastate.action", exc_info=True)

    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "action": event_payload["id"],
        "name": name or None,
        "request_id": request_id or None,
        "operation_id": None,
        "result": {
            "ok": True,
            "accepted": True,
            "action": event_payload["id"],
            "name": name or None,
            "request_id": request_id or None,
            "deferred_snapshot": True,
            "full_snapshot_removed": True,
        },
        "snapshot": {},
    }


@router.post("/infra_access/action", dependencies=[Depends(require_token)])
async def node_infra_access_action(payload: InfraAccessActionRequest) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(payload.webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    ctx = get_ctx()
    mgr = SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )
    action_id = str(payload.id or "").strip().lower()
    target_id = str(payload.target_id or "").strip() or None

    def _run() -> tuple[dict[str, Any], dict[str, Any]]:
        if action_id == "refresh":
            snapshot = mgr.run_tool(
                "infra_access_skill",
                "refresh_snapshot",
                {
                    "webspace_id": target_webspace_id,
                    "target_id": target_id,
                },
            )
            return (
                {"ok": True, "accepted": True, "action": action_id},
                snapshot if isinstance(snapshot, dict) else {"raw": snapshot},
            )
        if action_id == "issue_codex_session":
            result = mgr.run_tool(
                "infra_access_skill",
                "issue_codex_connection",
                {
                    "webspace_id": target_webspace_id,
                    "target_id": target_id,
                    "capability_profile": str(payload.capability_profile or "ProfileOpsRead"),
                    "ttl_seconds": int(payload.ttl_seconds or 28_800),
                },
            )
            snapshot = mgr.run_tool(
                "infra_access_skill",
                "get_snapshot",
                {
                    "webspace_id": target_webspace_id,
                    "target_id": target_id,
                },
            )
            return (
                result if isinstance(result, dict) else {"ok": True, "accepted": True, "action": action_id, "raw": result},
                snapshot if isinstance(snapshot, dict) else {"raw": snapshot},
            )
        raise HTTPException(status_code=400, detail=f"unsupported infra_access action: {action_id}")

    try:
        result, snapshot = await anyio.to_thread.run_sync(_run)
    except HTTPException:
        raise
    except Exception as exc:
        _log.warning("node infra_access action failed webspace=%s action=%s", target_webspace_id, action_id, exc_info=True)
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "action": action_id,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": bool(result.get("ok", True)),
        "accepted": True,
        "webspace_id": target_webspace_id,
        "action": action_id,
        "result": result,
        "snapshot": snapshot,
    }


@router.get("/yjs/webspaces", dependencies=[Depends(require_token)])
async def node_yjs_webspaces() -> dict[str, Any]:
    conf = load_config()
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "error": "hub_role_required",
        }
    items = [
        {
            "id": item.id,
            "title": item.title,
            "created_at": item.created_at,
            "kind": item.kind,
            "home_scenario": item.home_scenario,
            "home_scenario_ref": getattr(item, "home_scenario_ref", None),
            "source_mode": item.source_mode,
            "node_id": getattr(item, "node_id", None) or _local_node_id(),
            "node_label": getattr(item, "node_label", None) or _local_node_label(),
            "node_compact_label": getattr(item, "node_compact_label", None),
            "node_index": getattr(item, "node_index", None),
            "node_color": getattr(item, "node_color", None),
            "current_scenario": getattr(item, "current_scenario", None),
            "stored_home_scenario_exists": getattr(item, "stored_home_scenario_exists", None),
            "home_scenario_exists": getattr(item, "home_scenario_exists", True),
            "current_scenario_exists": getattr(item, "current_scenario_exists", None),
            "degraded": getattr(item, "degraded", False),
            "validation_reason": getattr(item, "validation_reason", None),
            "recommended_action": getattr(item, "recommended_action", None),
        }
        for item in WebspaceService().list(mode="mixed")
    ]
    return {
        "ok": True,
        "accepted": True,
        "catalog_version": workspace_index.workspace_catalog_version(),
        "items": items,
    }


@router.post("/yjs/webspaces", dependencies=[Depends(require_token)])
async def node_yjs_create_webspace(payload: WebspaceCreateRequest) -> dict[str, Any]:
    conf = load_config()
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "error": "hub_role_required",
        }
    scenario_id = str(payload.scenario_id or "").strip() or "web_desktop"
    info = await WebspaceService().create(
        str(payload.id or "").strip() or None,
        str(payload.title or "").strip() or None,
        scenario_id=scenario_id,
        scenario_ref=payload.scenario_ref if isinstance(payload.scenario_ref, dict) else None,
        dev=bool(payload.dev),
    )
    return {
        "ok": True,
        "accepted": True,
        "webspace": {
            "id": info.id,
            "title": info.title,
            "created_at": info.created_at,
            "kind": info.kind,
            "home_scenario": info.home_scenario,
            "home_scenario_ref": getattr(info, "home_scenario_ref", None),
            "source_mode": info.source_mode,
        },
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=info.id,
        ),
    }


@router.get("/yjs/webspaces/{webspace_id}/runtime", dependencies=[Depends(require_token)])
async def node_yjs_webspace_runtime(webspace_id: str) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    return {
        "ok": True,
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.get("/yjs/webspaces/{webspace_id}", dependencies=[Depends(require_token)])
async def node_yjs_webspace_state(webspace_id: str) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    state = await describe_webspace_operational_state(target_webspace_id)
    validation = await describe_webspace_validation_state(target_webspace_id)
    overlay = describe_webspace_overlay_state(target_webspace_id)
    projection = await describe_webspace_projection_state(target_webspace_id)
    rebuild = describe_webspace_rebuild_state(target_webspace_id)
    desktop = (await WebDesktopService().get_snapshot_async(target_webspace_id)).to_dict()
    materialization = await _describe_yjs_materialization(target_webspace_id, rebuild_state=rebuild)
    return {
        "ok": True,
        "accepted": True,
        "webspace": state.to_dict(),
        "validation": validation,
        "overlay": overlay,
        "desktop": desktop,
        "projection": projection,
        "rebuild": rebuild,
        "materialization": materialization,
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.get("/yjs/webspaces/{webspace_id}/validation", dependencies=[Depends(require_token)])
async def node_yjs_webspace_validation_state(webspace_id: str) -> dict[str, Any]:
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "validation": await describe_webspace_validation_state(target_webspace_id),
    }


@router.get("/yjs/webspaces/{webspace_id}/rebuild", dependencies=[Depends(require_token)])
async def node_yjs_webspace_rebuild_state(
    webspace_id: str,
    include_runtime: bool = False,
) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    rebuild = describe_webspace_rebuild_state(target_webspace_id)
    result = {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "rebuild": rebuild,
    }
    if include_runtime:
        result["runtime"] = yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        )
    return result


@router.get("/yjs/webspaces/{webspace_id}/materialization", dependencies=[Depends(require_token)])
async def node_yjs_webspace_materialization_state(
    webspace_id: str,
    include_runtime: bool = False,
    verify_live: bool = False,
) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    rebuild = describe_webspace_rebuild_state(target_webspace_id)
    materialization = await _describe_yjs_materialization(
        target_webspace_id,
        rebuild_state=rebuild,
        verify_live=verify_live,
    )
    result = {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "materialization": materialization,
        "rebuild": rebuild,
        "live_verification": bool(verify_live),
    }
    if include_runtime:
        result["runtime"] = yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        )
    return result


@router.get("/yjs/webspaces/{webspace_id}/materialization/snapshot", dependencies=[Depends(require_token)])
async def node_yjs_webspace_materialization_snapshot(
    webspace_id: str,
    include_runtime: bool = False,
    scope: str = "essential",
) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    snapshot_scope = "full" if str(scope or "").strip().lower() == "full" else "essential"
    rebuild = describe_webspace_rebuild_state(target_webspace_id)
    degraded = False
    try:
        operational_state = await describe_webspace_operational_state(target_webspace_id)
        expected_effective_scenario = (
            str(getattr(operational_state, "current_scenario", None) or "").strip()
            or str(getattr(operational_state, "effective_home_scenario", None) or "").strip()
            or None
        )
        materialization = await _describe_yjs_materialization(
            target_webspace_id,
            rebuild_state=rebuild,
            verify_live=False,
        )
        expected_snapshot_scenario = (
            expected_effective_scenario
            or str(materialization.get("current_scenario") or "").strip()
            or str(rebuild.get("scenario_id") or "").strip()
            or None
        )
        payload_snapshot = _materialized_payload_to_snapshot(
            target_webspace_id,
            get_webspace_rebuild_materialized_payload(target_webspace_id),
            scope=snapshot_scope,
        )
        if payload_snapshot is not None:
            payload_materialization = _describe_materialization_snapshot_payload(
                target_webspace_id,
                payload_snapshot,
                rebuild_state=rebuild,
                source="rebuild_materialized_payload",
            )
            payload_scenario = str(payload_materialization.get("current_scenario") or "").strip() or None
            if (
                bool(payload_materialization.get("ready"))
                and (
                    bool(expected_snapshot_scenario)
                    and bool(payload_scenario)
                    and payload_scenario == expected_snapshot_scenario
                )
            ):
                snapshot = payload_snapshot
                if not bool(materialization.get("ready")):
                    materialization = payload_materialization
                else:
                    materialization = dict(materialization)
                    materialization["snapshot_validation"] = {
                        "ready": True,
                        "readiness_state": payload_materialization.get("readiness_state"),
                        "missing_branches": [],
                        "snapshot_source": "rebuild_materialized_payload",
                    }
                seed_health = _materialization_seed_health(
                    state="ready",
                    reason="rebuild_materialized_payload",
                    source="rebuild_materialized_payload",
                    stale=False,
                    last_good_snapshot_at=(
                        materialization.get("observed_at")
                        or rebuild.get("finished_at")
                        or rebuild.get("updated_at")
                    ),
                    timeout_s=_YJS_MATERIALIZATION_SNAPSHOT_TIMEOUT_S,
                )
                degraded = False
            else:
                payload_snapshot = None
        else:
            payload_snapshot = None
        if payload_snapshot is None:
            snapshot = await asyncio.wait_for(
                _read_yjs_materialization_snapshot(
                    target_webspace_id,
                    scope=snapshot_scope,
                    prefer_live_room=False,
                ),
                timeout=_YJS_MATERIALIZATION_SNAPSHOT_TIMEOUT_S,
            )
            snapshot_materialization = _describe_materialization_snapshot_payload(
                target_webspace_id,
                snapshot,
                rebuild_state=rebuild,
                source="disk_snapshot",
            )
            snapshot_scenario = str(snapshot_materialization.get("current_scenario") or "").strip() or None
            if expected_snapshot_scenario and snapshot_scenario and snapshot_scenario != expected_snapshot_scenario:
                snapshot_materialization = dict(snapshot_materialization)
                snapshot_materialization["ready"] = False
                snapshot_materialization["readiness_state"] = "degraded"
                missing = list(snapshot_materialization.get("missing_branches") or [])
                if "ui.current_scenario" not in missing:
                    missing.append("ui.current_scenario")
                snapshot_materialization["missing_branches"] = missing
                snapshot_materialization["materialization_mismatch"] = True
                snapshot_materialization["expected_current_scenario"] = expected_snapshot_scenario
                snapshot_materialization["stale"] = True
                snapshot_materialization["stale_reason"] = "disk_snapshot_scenario_mismatch"
                snapshot_materialization["cache_fresh"] = False
            if bool(snapshot_materialization.get("ready")):
                if not bool(materialization.get("ready")):
                    materialization = snapshot_materialization
                else:
                    materialization = dict(materialization)
                    materialization["snapshot_validation"] = {
                        "ready": True,
                        "readiness_state": snapshot_materialization.get("readiness_state"),
                        "missing_branches": [],
                        "snapshot_source": "disk_snapshot",
                    }
            else:
                materialization = snapshot_materialization
            seed_health = _materialization_seed_health(
                state="ready" if bool(materialization.get("ready")) else "degraded",
                reason=(
                    "disk_snapshot_read"
                    if bool(materialization.get("ready"))
                    else str(materialization.get("readiness_state") or "materialization_cache_missing")
                ),
                source="disk_snapshot",
                stale=not bool(materialization.get("ready")),
                last_good_snapshot_at=(
                    materialization.get("observed_at")
                    or rebuild.get("finished_at")
                    or rebuild.get("updated_at")
                ),
                timeout_s=_YJS_MATERIALIZATION_SNAPSHOT_TIMEOUT_S,
            )
            degraded = bool(seed_health.get("state") != "ready")
    except asyncio.TimeoutError:
        fallback = _fallback_materialization_snapshot_from_cache(
            target_webspace_id,
            rebuild_state=rebuild,
            reason="ystore_read_timeout",
            error=f"snapshot read exceeded {_YJS_MATERIALIZATION_SNAPSHOT_TIMEOUT_S:.3f}s",
        )
        snapshot = fallback["snapshot"]
        materialization = fallback["materialization"]
        seed_health = fallback["seed_health"]
        degraded = True
    except Exception as exc:
        fallback = _fallback_materialization_snapshot_from_cache(
            target_webspace_id,
            rebuild_state=rebuild,
            reason="materialization_cache_missing",
            error=f"{type(exc).__name__}: {exc}",
        )
        snapshot = fallback["snapshot"]
        materialization = fallback["materialization"]
        seed_health = fallback["seed_health"]
        degraded = True
    result = {
        "ok": True,
        "accepted": True,
        "degraded": degraded,
        "state": "degraded" if degraded else "ready",
        "reason": seed_health.get("reason"),
        "stale": bool(seed_health.get("stale")),
        "source": seed_health.get("source"),
        "last_good_snapshot_at": seed_health.get("last_good_snapshot_at"),
        "webspace_id": target_webspace_id,
        "snapshot_scope": snapshot_scope,
        "snapshot": snapshot,
        "materialization": materialization,
        "seed_health": seed_health,
        "rebuild": rebuild,
    }
    if include_runtime:
        result["runtime"] = yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        )
    return result


@router.post("/yjs/webspaces/{webspace_id}/materialization/repair", dependencies=[Depends(require_token)])
async def node_yjs_webspace_materialization_repair(
    webspace_id: str,
    payload: WebspaceMaterializationRepairRequest,
    request: Request,
) -> dict[str, Any]:
    """Republish the authoritative live YDoc without replacing its room."""

    started = time.perf_counter()
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }

    materialized_payload = await asyncio.to_thread(
        get_webspace_rebuild_materialized_payload,
        target_webspace_id,
    )
    if not materialized_payload:
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "authoritative_materialization_unavailable",
            "rebuild": describe_webspace_rebuild_state(target_webspace_id),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    expected_scenario = str(payload.expected_scenario or "").strip()
    authoritative_scenario = str(
        materialized_payload.get("scenario_id")
        or _coerce_dict(materialized_payload.get("ui")).get("current_scenario")
        or ""
    ).strip()
    if expected_scenario and authoritative_scenario != expected_scenario:
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "authoritative_scenario_mismatch",
            "expected_scenario": expected_scenario,
            "authoritative_scenario": authoritative_scenario or None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    missing_branches = list(
        dict.fromkeys(
            token
            for token in (
                str(item or "").strip()[:256]
                for item in payload.missing_branches[:32]
            )
            if token
        )
    )
    event_payload = _trace_yjs_control_ingress(
        request=request,
        kind="desktop.webspace.materialization.repair",
        webspace_id=target_webspace_id,
        scenario_id=authoritative_scenario or None,
    )
    repair, coalesced = await _coalesced_materialization_repair(
        target_webspace_id,
        materialized_payload=materialized_payload,
    )
    applied = bool(repair.get("ok")) and bool(repair.get("materialized_payload_applied"))
    materialized_apply = _coerce_dict(repair.get("materialized_payload"))
    result = {
        "ok": applied,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "scenario_id": authoritative_scenario or None,
        "requested_missing_branches": missing_branches,
        "request_id": str(payload.request_id or "").strip() or _coerce_dict(event_payload.get("_meta")).get("cmd_id"),
        "request_source": str(payload.request_source or "").strip() or "browser_yjs_materialization_repair",
        "room_preserved": not bool(repair.get("room_dropped")),
        "transport_connections_closed": int(repair.get("closed_connections") or 0),
        "full_state_update": bool(repair.get("force_full_state_update")),
        "coalesced": bool(coalesced),
        "full_state_update_bytes": int(materialized_apply.get("full_state_update_bytes") or 0),
        "direct_client_broadcast_count": int(materialized_apply.get("direct_client_broadcast_count") or 0),
        "direct_client_broadcast_failed": int(materialized_apply.get("direct_client_broadcast_failed") or 0),
        "repair": repair,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
    if not applied:
        result["error"] = str(materialized_apply.get("error") or repair.get("error") or "live_room_repair_failed")
    _log.log(
        logging.INFO if applied else logging.WARNING,
        "Yjs materialization repair webspace=%s scenario=%s missing=%s applied=%s full_bytes=%s clients=%s failed=%s elapsed_ms=%s",
        target_webspace_id,
        authoritative_scenario or "-",
        missing_branches,
        applied,
        result["full_state_update_bytes"],
        result["direct_client_broadcast_count"],
        result["direct_client_broadcast_failed"],
        result["elapsed_ms"],
    )
    _publish_yjs_control_event(
        action="materialization_repair",
        webspace_id=target_webspace_id,
        result=result,
        scenario_id=authoritative_scenario or None,
    )
    return result


@router.patch("/yjs/webspaces/{webspace_id}", dependencies=[Depends(require_token)])
async def node_yjs_update_webspace(webspace_id: str, payload: WebspaceUpdateRequest) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    update_kwargs: dict[str, Any] = {
        "title": str(payload.title or "").strip() or None,
        "home_scenario": str(payload.home_scenario or "").strip() or None,
    }
    if "home_scenario_ref" in getattr(payload, "model_fields_set", set()):
        update_kwargs["home_scenario_ref"] = payload.home_scenario_ref
    info = await WebspaceService().update_metadata(
        target_webspace_id,
        **update_kwargs,
    )
    if info is None:
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "webspace_not_found",
        }
    return {
        "ok": True,
        "accepted": True,
        "webspace": {
            "id": info.id,
            "title": info.title,
            "created_at": info.created_at,
            "kind": info.kind,
            "home_scenario": info.home_scenario,
            "home_scenario_ref": getattr(info, "home_scenario_ref", None),
            "source_mode": info.source_mode,
        },
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.post("/yjs/webspaces/{webspace_id}/backup", dependencies=[Depends(require_token)])
async def node_yjs_backup(webspace_id: str) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    store = get_ystore_for_webspace(target_webspace_id)
    await store.backup_to_disk()
    result = {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }
    _publish_yjs_control_event(
        action="backup",
        webspace_id=target_webspace_id,
        result=result,
    )
    return result


@router.post("/yjs/webspaces/{webspace_id}/reload", dependencies=[Depends(require_token)])
async def node_yjs_reload(webspace_id: str, payload: WebspaceYjsActionRequest, request: Request) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    scenario_id = str(payload.scenario_id or "").strip() or None
    recreate_room_requested = bool(payload.recreate_room)
    requested_action = "reset" if recreate_room_requested else "reload"
    event_payload = _trace_yjs_control_ingress(
        request=request,
        kind="desktop.webspace.reload",
        webspace_id=target_webspace_id,
        scenario_id=scenario_id,
        recreate_room=recreate_room_requested,
    )
    runtime_before = yjs_sync_runtime_snapshot(
        role=conf.role,
        webspace_id=target_webspace_id,
    )
    result = await reload_webspace_from_scenario(
        target_webspace_id,
        scenario_id=scenario_id,
        action=requested_action,
        event_payload=event_payload,
    )
    result["yws_guard_reset"] = _clear_reload_yws_guard_state(
        target_webspace_id,
        reason=f"node_yjs_reload:{requested_action}",
    )
    result = _attach_runtime_and_rebuild(
        result,
        role=conf.role,
        webspace_id=target_webspace_id,
        include_rebuild=recreate_room_requested,
    )
    result = _attach_yjs_action_debug(
        result,
        requested_endpoint="reload",
        recreate_room_requested=recreate_room_requested,
        runtime_before=runtime_before,
    )
    _publish_yjs_control_event(
        action="reload",
        webspace_id=target_webspace_id,
        result=result,
        scenario_id=scenario_id,
    )
    return result


@router.post("/yjs/webspaces/{webspace_id}/toggle-install", dependencies=[Depends(require_token)])
async def node_yjs_toggle_install(webspace_id: str, payload: WebspaceToggleInstallRequest) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    svc = WebDesktopService()
    await svc.toggle_install_async(str(payload.type), str(payload.id), target_webspace_id)
    installed = await svc.get_installed_async(target_webspace_id)
    desktop = await svc.get_snapshot_async(target_webspace_id)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "type": str(payload.type),
        "id": str(payload.id),
        "installed": installed.to_dict(),
        "desktop": desktop.to_dict(),
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.get("/yjs/webspaces/{webspace_id}/desktop", dependencies=[Depends(require_token)])
async def node_yjs_desktop_state(webspace_id: str) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    desktop = await WebDesktopService().get_snapshot_async(target_webspace_id)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "desktop": desktop.to_dict(),
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.get("/yjs/webspaces/{webspace_id}/catalog/{kind}", dependencies=[Depends(require_token)])
async def node_yjs_catalog_state(webspace_id: str, kind: str) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    normalized_kind = "widgets" if str(kind or "").strip().lower() == "widgets" else "apps"
    rebuild = describe_webspace_rebuild_state(target_webspace_id)
    materialization = await _describe_yjs_materialization(target_webspace_id, rebuild_state=rebuild)
    items = await _materialize_catalog_items(target_webspace_id, normalized_kind)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "kind": normalized_kind,
        "items": items,
        "materialization": materialization,
        "rebuild": rebuild,
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.post("/yjs/webspaces/{webspace_id}/desktop/pinned-widgets", dependencies=[Depends(require_token)])
async def node_yjs_set_pinned_widgets(
    webspace_id: str,
    payload: WebspacePinnedWidgetsRequest,
) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    svc = WebDesktopService()
    svc.set_pinned_widgets_with_live_room(list(payload.pinnedWidgets or []), target_webspace_id)
    desktop = await svc.get_snapshot_async(target_webspace_id)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "desktop": desktop.to_dict(),
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.patch("/yjs/webspaces/{webspace_id}/desktop", dependencies=[Depends(require_token)])
async def node_yjs_update_desktop(
    webspace_id: str,
    payload: WebspaceDesktopUpdateRequest,
) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    svc = WebDesktopService()
    if payload.installed is not None:
        installed = payload.installed if isinstance(payload.installed, dict) else {}
        svc.set_installed_with_live_room(
            WebDesktopInstalled(
                apps=list(installed.get("apps") or []),
                widgets=list(installed.get("widgets") or []),
                removed_apps=list(installed.get("removedApps") or installed.get("removed_apps") or []),
                removed_widgets=list(installed.get("removedWidgets") or installed.get("removed_widgets") or []),
            ),
            target_webspace_id,
        )
    if payload.pinnedWidgets is not None:
        svc.set_pinned_widgets_with_live_room(list(payload.pinnedWidgets or []), target_webspace_id)
    if payload.topbar is not None:
        svc.set_topbar_with_live_room(list(payload.topbar or []), target_webspace_id)
    if payload.pageSchema is not None:
        svc.set_page_schema_with_live_room(dict(payload.pageSchema or {}), target_webspace_id)
    if payload.iconOrder is not None:
        svc.set_icon_order_with_live_room(
            [str(item or "").strip() for item in payload.iconOrder if str(item or "").strip()],
            target_webspace_id,
        )
    if payload.widgetOrder is not None:
        svc.set_widget_order_with_live_room(
            [str(item or "").strip() for item in payload.widgetOrder if str(item or "").strip()],
            target_webspace_id,
        )
    if payload.hiddenSections is not None:
        svc.set_hidden_sections_with_live_room(
            [str(item or "").strip() for item in payload.hiddenSections if str(item or "").strip()],
            target_webspace_id,
        )
    desktop = await svc.get_snapshot_async(target_webspace_id)
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace_id,
        "desktop": desktop.to_dict(),
        "runtime": yjs_sync_runtime_snapshot(
            role=conf.role,
            webspace_id=target_webspace_id,
        ),
    }


@router.post("/yjs/webspaces/{webspace_id}/scenario", dependencies=[Depends(require_token)])
async def node_yjs_switch_scenario(
    webspace_id: str,
    payload: WebspaceYjsActionRequest,
    request: Request,
) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    scenario_id = str(payload.scenario_id or "").strip()
    if not scenario_id:
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "scenario_id_required",
        }
    requested_wait_for_rebuild = bool(payload.wait_for_rebuild) if payload.wait_for_rebuild is not None else False
    effective_wait_for_rebuild = False
    request_source = (
        str(payload.request_source or "").strip()
        or str(request.headers.get("x-adaos-source") or request.headers.get("x-request-source") or "").strip()
        or "api.node.yjs.switch_scenario"
    )
    request_id = (
        str(payload.request_id or "").strip()
        or str(request.headers.get("x-request-id") or request.headers.get("x-trace-id") or "").strip()
        or None
    )
    request_client = _request_client_label(request, endpoint="node_yjs_switch_scenario")
    result = await switch_webspace_scenario(
        target_webspace_id,
        scenario_id,
        set_home=payload.set_home,
        wait_for_rebuild=effective_wait_for_rebuild,
        request_id=request_id,
        request_source=request_source,
        request_client=request_client,
    )
    result = _attach_wait_for_rebuild_guard(
        result,
        requested=requested_wait_for_rebuild,
        effective=effective_wait_for_rebuild,
        reason="scenario_switch_rebuild_runs_in_background_to_protect_route_budget",
    )
    if bool(payload.include_runtime) or bool(payload.include_rebuild):
        result = _attach_runtime_and_rebuild(
            result,
            role=conf.role,
            webspace_id=target_webspace_id,
            include_rebuild=bool(payload.include_rebuild),
        )
    _publish_yjs_control_event(
        action="scenario",
        webspace_id=target_webspace_id,
        result=result,
        scenario_id=scenario_id,
    )
    return result


@router.post("/yjs/webspaces/{webspace_id}/go-home", dependencies=[Depends(require_token)])
async def node_yjs_go_home(
    webspace_id: str,
    payload: WebspaceYjsActionRequest | None = None,
) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    requested_wait_for_rebuild = bool(payload.wait_for_rebuild) if payload and payload.wait_for_rebuild is not None else False
    effective_wait_for_rebuild = False
    result = await go_home_webspace(
        target_webspace_id,
        wait_for_rebuild=effective_wait_for_rebuild,
    )
    result = _attach_wait_for_rebuild_guard(
        result,
        requested=requested_wait_for_rebuild,
        effective=effective_wait_for_rebuild,
        reason="go_home_rebuild_runs_in_background_to_protect_route_budget",
    )
    if payload and (bool(payload.include_runtime) or bool(payload.include_rebuild)):
        result = _attach_runtime_and_rebuild(
            result,
            role=conf.role,
            webspace_id=target_webspace_id,
            include_rebuild=bool(payload.include_rebuild),
        )
    _publish_yjs_control_event(
        action="go_home",
        webspace_id=target_webspace_id,
        result=result,
        scenario_id=str(result.get("scenario_id") or result.get("home_scenario") or "").strip() or None,
    )
    return result


@router.post("/yjs/dev-webspaces/ensure", dependencies=[Depends(require_token)])
async def node_yjs_ensure_dev(payload: WebspaceYjsActionRequest) -> dict[str, Any]:
    conf = load_config()
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "error": "hub_role_required",
        }
    scenario_id = str(payload.scenario_id or "").strip()
    if not scenario_id:
        return {
            "ok": False,
            "accepted": False,
            "error": "scenario_id_required",
        }
    result = await ensure_dev_webspace_for_scenario(
        scenario_id,
        requested_id=str(payload.requested_id or "").strip() or None,
        title=str(payload.title or "").strip() or None,
    )
    target_webspace_id = _coerce_node_webspace_id(result.get("webspace_id"))
    result["runtime"] = yjs_sync_runtime_snapshot(
        role=conf.role,
        webspace_id=target_webspace_id,
    )
    _publish_yjs_control_event(
        action="ensure_dev",
        webspace_id=target_webspace_id,
        result=result,
        scenario_id=scenario_id,
    )
    return result


@router.post("/yjs/webspaces/{webspace_id}/set-home", dependencies=[Depends(require_token)])
async def node_yjs_set_home(webspace_id: str, payload: WebspaceYjsActionRequest) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    scenario_id = str(payload.scenario_id or "").strip()
    if not scenario_id:
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "scenario_id_required",
        }
    set_home_kwargs: dict[str, Any] = {}
    if "home_scenario_ref" in getattr(payload, "model_fields_set", set()):
        set_home_kwargs["home_scenario_ref"] = payload.home_scenario_ref
    elif "scenario_ref" in getattr(payload, "model_fields_set", set()):
        set_home_kwargs["home_scenario_ref"] = payload.scenario_ref
    info = await WebspaceService().set_home_scenario(
        target_webspace_id,
        scenario_id,
        **set_home_kwargs,
    )
    result: dict[str, Any]
    if info is None:
        result = {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "scenario_id": scenario_id,
            "error": "webspace_not_found",
        }
    else:
        result = {
            "ok": True,
            "accepted": True,
            "webspace_id": info.id,
            "scenario_id": scenario_id,
            "home_scenario": info.home_scenario,
            "home_scenario_ref": getattr(info, "home_scenario_ref", None),
        }
    result["runtime"] = yjs_sync_runtime_snapshot(
        role=conf.role,
        webspace_id=target_webspace_id,
    )
    _publish_yjs_control_event(
        action="set_home",
        webspace_id=target_webspace_id,
        result=result,
        scenario_id=scenario_id,
    )
    return result


@router.post("/yjs/webspaces/{webspace_id}/set-home-current", dependencies=[Depends(require_token)])
async def node_yjs_set_home_current(webspace_id: str) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    result = await set_current_webspace_home(target_webspace_id)
    result["runtime"] = yjs_sync_runtime_snapshot(
        role=conf.role,
        webspace_id=target_webspace_id,
    )
    _publish_yjs_control_event(
        action="set_home_current",
        webspace_id=target_webspace_id,
        result=result,
        scenario_id=str(result.get("scenario_id") or result.get("home_scenario") or "").strip() or None,
    )
    return result


@router.post("/yjs/webspaces/{webspace_id}/reset", dependencies=[Depends(require_token)])
async def node_yjs_reset(webspace_id: str, payload: WebspaceYjsActionRequest, request: Request) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    runtime_before = yjs_sync_runtime_snapshot(
        role=conf.role,
        webspace_id=target_webspace_id,
    )
    event_payload = _trace_yjs_control_ingress(
        request=request,
        kind="desktop.webspace.reset",
        webspace_id=target_webspace_id,
        scenario_id=str(payload.scenario_id or "").strip() or None,
        recreate_room=True,
    )
    result = await reload_webspace_from_scenario(
        target_webspace_id,
        scenario_id=str(payload.scenario_id or "").strip() or None,
        action="reset",
        event_payload=event_payload,
    )
    result["yws_guard_reset"] = _clear_reload_yws_guard_state(
        target_webspace_id,
        reason="node_yjs_reset",
    )
    result = _attach_runtime_and_rebuild(
        result,
        role=conf.role,
        webspace_id=target_webspace_id,
        include_rebuild=True,
    )
    result = _attach_yjs_action_debug(
        result,
        requested_endpoint="reset",
        recreate_room_requested=True,
        runtime_before=runtime_before,
    )
    _publish_yjs_control_event(
        action="reset",
        webspace_id=target_webspace_id,
        result=result,
        scenario_id=str(payload.scenario_id or "").strip() or None,
    )
    return result


@router.post("/yjs/webspaces/{webspace_id}/restore", dependencies=[Depends(require_token)])
async def node_yjs_restore(webspace_id: str) -> dict[str, Any]:
    conf = load_config()
    target_webspace_id = _coerce_node_webspace_id(webspace_id)
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "webspace_id": target_webspace_id,
            "error": "hub_role_required",
        }
    result = await restore_webspace_from_snapshot(target_webspace_id)
    result = _attach_runtime_and_rebuild(
        result,
        role=conf.role,
        webspace_id=target_webspace_id,
        include_rebuild=True,
    )
    _publish_yjs_control_event(
        action="restore",
        webspace_id=target_webspace_id,
        result=result,
    )
    return result


def _tracked_media_file_range(
    resource: MediaResource,
    *,
    start: int,
    end: int,
) -> Iterator[bytes]:
    lease_id = begin_media_delivery(media_type=resource.mime_type)
    try:
        for chunk in file_range_iter(resource.path, start=start, end=end):
            touch_media_delivery(lease_id)
            yield chunk
    finally:
        end_media_delivery(lease_id)


def _stream_media_resource(resource: MediaResource, request: Request) -> StreamingResponse | Response:
    target = resource.path
    size = int(resource.size_bytes)
    try:
        byte_range = parse_media_range(request.headers.get("range"), size=size)
    except Exception:
        return Response(
            status_code=416,
            content=b"range_not_satisfiable",
            headers={"Content-Range": f"bytes */{size}"},
            media_type="text/plain",
        )
    status_code, _reason, headers, start, end = media_content_response_parts(
        filename=resource.name or target.name,
        mime_type=resource.mime_type,
        size=size,
        byte_range=byte_range,
        include_content_type=False,
    )
    return StreamingResponse(
        _tracked_media_file_range(resource, start=start, end=end),
        status_code=status_code,
        media_type=resource.mime_type,
        headers=headers,
    )


@router.get("/media-indexer/content/{playback_id}")
async def media_indexer_file_content(
    playback_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_adaos_token: str | None = Header(default=None),
):
    await _require_request_token(
        request,
        authorization=authorization,
        x_adaos_token=x_adaos_token,
    )
    try:
        resource = await asyncio.to_thread(resolve_media_indexer_resource, playback_id)
    except ValueError as exc:
        _raise_400(str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return _stream_media_resource(resource, request)


@router.get("/media/resources/content/{resource_id}")
async def media_reference_content(
    resource_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_adaos_token: str | None = Header(default=None),
):
    await _require_request_token(
        request,
        authorization=authorization,
        x_adaos_token=x_adaos_token,
    )
    try:
        resource = await asyncio.to_thread(resolve_media_reference, resource_id)
    except ValueError as exc:
        _raise_400(str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return _stream_media_resource(resource, request)


@router.get("/media/files", dependencies=[Depends(require_token)])
async def list_media_library() -> dict[str, Any]:
    snapshot = media_snapshot()
    snapshot["proxy_limits"] = {
        "root_routed_response_limit_bytes": ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES,
        "root_media_relay_max_upload_bytes": ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES,
    }
    return snapshot


@router.get("/media/runtime", dependencies=[Depends(require_token)])
async def media_runtime() -> dict[str, Any]:
    conf = load_config()
    runtime = media_plane_runtime_snapshot(
        role=str(getattr(conf, "role", "") or ""),
        route_mode=None,
        connected_to_hub=None,
    )
    runtime["ok"] = True
    runtime["proxy_limits"] = {
        "root_routed_response_limit_bytes": ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES,
        "root_media_relay_max_upload_bytes": ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES,
    }
    runtime["capabilities"] = media_capabilities()
    runtime["files"] = {
        "items": list_media_files(),
    }
    return runtime


@router.put("/media/files/{filename}", dependencies=[Depends(require_token)])
async def upload_media_file(filename: str, request: Request) -> dict[str, Any]:
    try:
        target = media_file_path(filename)
    except ValueError as exc:
        _raise_400(str(exc))

    replaced = target.exists()
    tmp_path = target.with_name(f"{target.name}.upload-{os.getpid()}-{id(request)}.part")
    total_bytes = 0
    try:
        with tmp_path.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                handle.write(chunk)
                total_bytes += len(chunk)
        tmp_path.replace(target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    return {
        "ok": True,
        "filename": target.name,
        "size_bytes": total_bytes,
        "mime_type": guess_media_type(target.name),
        "replaced": replaced,
    }


@router.delete("/media/files/{filename}", dependencies=[Depends(require_token)])
async def delete_media_file(filename: str) -> dict[str, Any]:
    try:
        target = media_file_path(filename)
    except ValueError as exc:
        _raise_400(str(exc))
    existed = target.exists()
    if existed:
        target.unlink()
    return {
        "ok": True,
        "filename": target.name,
        "deleted": existed,
        "items": list_media_files(),
    }


def _resolve_skill_asset_file(skill_name: str, asset_path: str) -> Path:
    skill_token = str(skill_name or "").strip()
    if not skill_token or not re.match(r"^[A-Za-z0-9_.-]+$", skill_token):
        _raise_400("invalid_skill_name")
    raw_path = str(asset_path or "").strip().replace("\\", "/")
    if not raw_path or raw_path.startswith("/") or "\x00" in raw_path:
        _raise_400("invalid_asset_path")
    try:
        relative = Path(raw_path)
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            _raise_400("invalid_asset_path")
        skill_dir = find_skill_dir(skill_token)
    except SkillDirectoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail="skill_not_found") from exc
    assets_root = (skill_dir / "assets").resolve()
    target = (assets_root / relative).resolve()
    try:
        target.relative_to(assets_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="asset_path_forbidden") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="skill_asset_not_found")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=404, detail="skill_asset_not_found") from exc
    if size > _BROWSER_RESOURCE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="skill_asset_too_large")
    return target


def _browser_resource_media_type(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _browser_resource_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@router.get("/skills/{skill_name}/assets/{asset_path:path}")
async def skill_asset_content(
    skill_name: str,
    asset_path: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_adaos_token: str | None = Header(default=None),
):
    await _require_request_token(
        request,
        authorization=authorization,
        x_adaos_token=x_adaos_token,
    )
    target = _resolve_skill_asset_file(skill_name, asset_path)
    digest = _browser_resource_sha256(target)
    etag = f'"sha256:{digest}"'
    headers = {
        "Cache-Control": "private, max-age=31536000, immutable",
        "ETag": etag,
        "X-AdaOS-Cache-Key": f"sha256:{digest}",
        "X-AdaOS-Resource-Scope": "skill",
        "X-AdaOS-Resource-Owner": str(skill_name),
    }
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return FileResponse(
        path=target,
        media_type=_browser_resource_media_type(target),
        headers=headers,
    )


@router.get("/media/files/content/{filename}")
async def media_file_content(
    filename: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_adaos_token: str | None = Header(default=None),
):
    await _require_request_token(
        request,
        authorization=authorization,
        x_adaos_token=x_adaos_token,
    )
    try:
        target = media_file_path(filename)
    except ValueError as exc:
        try:
            resource = resolve_media_indexer_resource_by_name(filename)
        except ValueError:
            _raise_400(str(exc))
        except PermissionError as idx_exc:
            raise HTTPException(status_code=403, detail=str(idx_exc))
        except FileNotFoundError:
            _raise_400(str(exc))
        return _stream_media_resource(resource, request)
    if not target.exists() or not target.is_file():
        try:
            resource = resolve_media_indexer_resource_by_name(filename)
        except ValueError as exc:
            _raise_400(str(exc))
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="media_file_not_found")
        return _stream_media_resource(resource, request)
    resource = media_resource_from_path(
        target,
        source="media_server",
        resource_id=target.name,
        mime_type=guess_media_type(target.name),
    )
    return _stream_media_resource(resource, request)


@router.get("/members", dependencies=[Depends(require_token)])
async def node_members() -> dict[str, Any]:
    conf = load_config()
    route_mode, connected = route_info(conf.role)
    lifecycle = runtime_lifecycle_snapshot()
    reliability = reliability_snapshot(
        node_id=conf.node_id,
        subnet_id=conf.subnet_id,
        role=conf.role,
        local_ready=is_ready(),
        node_state=str(lifecycle.get("node_state") or "ready"),
        draining=bool(lifecycle.get("draining")),
        route_mode=route_mode,
        connected_to_hub=connected,
        node_names=list(getattr(conf, "node_names", []) or []),
    )
    runtime = reliability.get("runtime") if isinstance(reliability.get("runtime"), dict) else {}
    return {
        "ok": True,
        "hub_member_connection_state": (
            runtime.get("hub_member_connection_state")
            if isinstance(runtime.get("hub_member_connection_state"), dict)
            else {}
        ),
    }


@router.post("/members/{node_id}/snapshot/request", dependencies=[Depends(require_token)])
async def request_member_snapshot(node_id: str) -> dict[str, Any]:
    conf = load_config()
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "node_id": node_id,
            "error": "hub_role_required",
        }
    from adaos.services.subnet.link_manager import get_hub_link_manager

    return await get_hub_link_manager().request_member_snapshot(node_id, reason="node_api")


@router.post("/members/{node_id}/update", dependencies=[Depends(require_token)])
async def request_member_update(node_id: str, payload: MemberUpdateRequest) -> dict[str, Any]:
    conf = load_config()
    if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
        return {
            "ok": False,
            "accepted": False,
            "node_id": node_id,
            "error": "hub_role_required",
        }
    action = "update" if str(payload.action or "").strip().lower() == "start" else str(payload.action or "").strip().lower()
    from adaos.services.subnet.link_manager import get_hub_link_manager

    return await get_hub_link_manager().request_member_update(
        node_id,
        action=action,
        target_rev=str(payload.target_rev or ""),
        target_version=str(payload.target_version or ""),
        countdown_sec=payload.countdown_sec,
        drain_timeout_sec=payload.drain_timeout_sec,
        signal_delay_sec=payload.signal_delay_sec,
        reason=str(payload.reason or "node_api.member_update"),
    )
