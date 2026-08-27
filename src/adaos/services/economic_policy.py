from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from adaos.services.bootstrap import load_config
from adaos.services.runtime_paths import current_base_dir, current_state_dir
from adaos.services.zone_hosts import (
    DEFAULT_PUBLIC_ROOT_BASE_URL,
    canonical_zone_id,
    zone_public_base_url,
)

ECONOMIC_STATUS_SCHEMA = "adaos.subnet.economic_status.v1"
ECONOMIC_ENTITLEMENT_SNAPSHOT_SCHEMA = "adaos.root_mgmnt.economic_entitlement.v1"
ROOT_GOVERNED_RESOURCES: tuple[str, ...] = (
    "llm.requests",
    "llm.tokens.input",
    "llm.tokens.output",
    "llm.tokens.reasoning",
    "codex.api.tokens",
    "root_mcp.calls",
    "skill.subscription_invocations",
    "background.jobs",
    "storage.bytes",
    "media.indexing",
    "external.integrations",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = _text(os.getenv(name)).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _int_value(value: Any) -> int:
    try:
        parsed = int(float(str(value)))
    except Exception:
        return 0
    return parsed if parsed > 0 else 0


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_yaml_file(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _load_config_best_effort(base_dir: Path) -> Any:
    try:
        return load_config()
    except Exception:
        pass
    data = _read_yaml_file((base_dir / "node.yaml").resolve())
    root = data.get("root")
    root_settings = root if isinstance(root, Mapping) else {}
    return SimpleNamespace(
        node_id=_text(os.getenv("ADAOS_NODE_ID")) or _text(data.get("node_id")),
        subnet_id=_text(os.getenv("ADAOS_SUBNET_ID")) or _text(data.get("subnet_id")),
        role=_text(os.getenv("ADAOS_ROLE")) or _text(data.get("role")) or "hub",
        zone_id=_text(os.getenv("ADAOS_ZONE_ID")) or _text(data.get("zone_id")),
        root_settings=SimpleNamespace(
            base_url=_text(os.getenv("ADAOS_ROOT_BASE_URL"))
            or _text(root_settings.get("base_url"))
            or _text(root_settings.get("api_base"))
        ),
    )


def entitlement_snapshot_path(*, base_dir: Path | None = None) -> Path:
    configured = _text(os.getenv("ADAOS_ECONOMIC_ENTITLEMENT_SNAPSHOT"))
    if configured:
        return Path(configured).expanduser().resolve()
    root = Path(base_dir) if base_dir is not None else current_base_dir()
    return (root / "state" / "economic_policy" / "entitlement_snapshot.json").resolve()


def _configured_zone_id(conf: Any) -> str:
    for value in (
        os.getenv("ADAOS_ZONE_ID"),
        getattr(conf, "zone_id", None),
        os.getenv("ADAOS_ROOT_ZONE"),
    ):
        token = _text(value).lower()
        if not token:
            continue
        return canonical_zone_id(token) or token
    return ""


def _root_base_url(conf: Any) -> str:
    return _text(getattr(getattr(conf, "root_settings", None), "base_url", None)).rstrip("/")


def _global_root_base_url() -> str:
    return _text(os.getenv("ADAOS_GLOBAL_ROOT_MGMNT_BASE_URL")).rstrip("/") or DEFAULT_PUBLIC_ROOT_BASE_URL


def _normalize_disabled_resources(raw: Any, *, reason_code: str, source: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raw = []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            resource = _text(item.get("resource"))
            if not resource:
                continue
            codes = item.get("reason_codes")
            reason_codes = [
                _text(code)
                for code in (codes if isinstance(codes, list) else [item.get("reason_code")])
                if _text(code)
            ]
            out.append(
                {
                    "resource": resource,
                    "reason_code": _text(item.get("reason_code")) or (reason_codes[0] if reason_codes else reason_code),
                    "reason": _text(item.get("reason")) or reason_code,
                    "source": _text(item.get("source")) or source,
                    "reason_codes": reason_codes or [reason_code],
                    "sources": list(item.get("sources")) if isinstance(item.get("sources"), list) else [source],
                }
            )
            continue
        resource = _text(item)
        if resource:
            out.append(
                {
                    "resource": resource,
                    "reason_code": reason_code,
                    "reason": reason_code,
                    "source": source,
                    "reason_codes": [reason_code],
                    "sources": [source],
                }
            )
    return out


def _default_disabled_resources() -> list[dict[str, Any]]:
    return [
        {
            "resource": resource,
            "reason_code": "entitlement_snapshot_missing",
            "reason": "No root economic entitlement snapshot is installed on this subnet",
            "source": "local_runtime_default",
            "reason_codes": ["entitlement_snapshot_missing"],
            "sources": ["local_runtime_default"],
        }
        for resource in ROOT_GOVERNED_RESOURCES
    ]


def _snapshot_subscription(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    subscription = raw.get("subscription")
    return subscription if isinstance(subscription, Mapping) else {}


def _snapshot_entitlement(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    entitlement = raw.get("entitlement")
    return entitlement if isinstance(entitlement, Mapping) else {}


def _state_dir_for_usage(base_dir: Path) -> Path:
    try:
        return current_state_dir()
    except Exception:
        return (base_dir / "state").resolve()


def _timestamp_s(value: Any) -> float:
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
    text = _text(value)
    if not text:
        return 0.0
    try:
        numeric = float(text)
        return numeric / 1000.0 if numeric > 10_000_000_000 else numeric
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _iso_from_timestamp(ts: float) -> str:
    if ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def _observed_llm_request_usage(base_dir: Path, *, now_ts: float | None = None) -> dict[str, Any]:
    state_dir = _state_dir_for_usage(base_dir)
    teacher_root = state_dir / "skills" / "nlu_teacher"
    if not teacher_root.exists():
        return {}
    now = float(now_ts or time.time())
    windows = {
        "used_24h": 24 * 60 * 60,
        "used_7d": 7 * 24 * 60 * 60,
        "used_30d": 30 * 24 * 60 * 60,
    }
    counts = {key: 0 for key in windows}
    status_counts: dict[str, int] = {}
    seen: set[str] = set()
    latest_ts = 0.0
    latest_model = ""
    latest_status = ""
    try:
        paths = sorted(teacher_root.glob("*.json"))[:100]
    except Exception:
        return {}
    for path in paths:
        payload = _read_json_file(path)
        logs = payload.get("llm_logs")
        if not isinstance(logs, list):
            continue
        for log in logs:
            if not isinstance(log, Mapping):
                continue
            event_id = _text(log.get("id") or log.get("log_id") or log.get("request_id"))
            if not event_id:
                event_id = _text(log.get("ts")) + ":" + _text(log.get("model"))
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            ts = max(
                _timestamp_s(log.get("ts")),
                _timestamp_s(log.get("created_at")),
                _timestamp_s(log.get("started_at")),
                _timestamp_s(log.get("finished_at")),
            )
            if ts <= 0:
                continue
            age_s = now - ts
            if age_s < 0:
                age_s = 0
            if age_s > windows["used_30d"]:
                continue
            status = _text(log.get("status")).lower() or "observed"
            if status in {"retrying", "queued"}:
                continue
            status_counts[status] = status_counts.get(status, 0) + 1
            for key, window_s in windows.items():
                if age_s <= window_s:
                    counts[key] += 1
            if ts >= latest_ts:
                latest_ts = ts
                latest_model = _text(log.get("model"))
                latest_status = status
    if not any(counts.values()):
        return {}
    return {
        "observed": True,
        **counts,
        "last_seen_at": _iso_from_timestamp(latest_ts),
        "last_model": latest_model,
        "last_status": latest_status,
        "source": "nlu_teacher.llm_logs",
        "status_counts": status_counts,
    }


def _merge_llm_request_usage(usage_payload: dict[str, Any], observed: Mapping[str, Any]) -> dict[str, Any]:
    if not observed:
        return usage_payload
    current = usage_payload.get("llm.requests")
    current_payload = dict(current) if isinstance(current, Mapping) else {}
    for key in ("used_24h", "used_7d", "used_30d", "denied_30d"):
        observed_value = _int_value(observed.get(key))
        current_value = _int_value(current_payload.get(key))
        if observed_value > current_value:
            current_payload[key] = observed_value
    for key in ("last_seen_at", "last_model", "last_status"):
        if _text(observed.get(key)) and not _text(current_payload.get(key)):
            current_payload[key] = _text(observed.get(key))
    current_payload["observed"] = True
    sources = current_payload.get("sources")
    source_values = [_text(item) for item in sources] if isinstance(sources, list) else []
    if _text(observed.get("source")) and _text(observed.get("source")) not in source_values:
        source_values.append(_text(observed.get("source")))
    if source_values:
        current_payload["sources"] = source_values
    if isinstance(observed.get("status_counts"), Mapping):
        current_payload["status_counts"] = dict(observed["status_counts"])
    usage_payload["llm.requests"] = current_payload
    return usage_payload


def current_subnet_economic_status() -> dict[str, Any]:
    base_dir = current_base_dir()
    conf = _load_config_best_effort(base_dir)
    zone_id = _configured_zone_id(conf)
    configured_root_base = _root_base_url(conf)
    global_root_base = _global_root_base_url()
    path = entitlement_snapshot_path(base_dir=base_dir)
    raw_snapshot = _read_json_file(path)
    subscription = _snapshot_subscription(raw_snapshot)
    entitlement = _snapshot_entitlement(raw_snapshot)
    loaded = bool(raw_snapshot)

    disabled_resources = _normalize_disabled_resources(
        raw_snapshot.get("disabled_resources") or entitlement.get("disabled_resources"),
        reason_code="entitlement_snapshot_missing",
        source="local_runtime_default",
    )
    if not loaded:
        disabled_resources = _default_disabled_resources()

    subscription_state = (
        _text(raw_snapshot.get("subscription_state"))
        or _text(subscription.get("state"))
        or ("unassigned" if not loaded else "unknown")
    )
    plan_id = _text(raw_snapshot.get("plan_id")) or _text(subscription.get("plan_id")) or "none"
    entitlement_state = (
        _text(raw_snapshot.get("entitlement_state"))
        or _text(entitlement.get("state"))
        or ("disabled_observed" if disabled_resources else "enabled")
    )
    mode = _text(raw_snapshot.get("mode") or raw_snapshot.get("enforcement_mode") or "observe").lower()
    if mode not in {"observe", "enforce"}:
        mode = "observe"
    usage = raw_snapshot.get("usage")
    usage_payload = dict(usage) if isinstance(usage, Mapping) else {}
    usage_payload = _merge_llm_request_usage(usage_payload, _observed_llm_request_usage(base_dir))
    global_report_enabled = _env_bool("ADAOS_GLOBAL_ROOT_MGMNT_REPORT_ENABLED", default=False)
    zone_root_base = zone_public_base_url(zone_id) if zone_id else ""

    return {
        "ok": True,
        "schema": ECONOMIC_STATUS_SCHEMA,
        "entitlement_schema": ECONOMIC_ENTITLEMENT_SNAPSHOT_SCHEMA,
        "generated_at": _now_iso(),
        "source": "root_entitlement_snapshot" if loaded else "local_runtime_default",
        "node_id": _text(getattr(conf, "node_id", None)),
        "subnet_id": _text(getattr(conf, "subnet_id", None)),
        "zone_id": zone_id,
        "subscription_state": subscription_state,
        "plan_id": plan_id,
        "entitlement_state": entitlement_state,
        "enforcement_mode": mode,
        "enforcement_active": mode == "enforce",
        "disabled_resource_count": len(disabled_resources),
        "disabled_resources": disabled_resources,
        "usage": usage_payload,
        "management_authority": {
            "source": "global_root",
            "global_base_url": global_root_base,
            "configured_root_base_url": configured_root_base,
            "zone_root_base_url": zone_root_base,
            "global_report_enabled": global_report_enabled,
            "cross_zone_expected": bool(zone_id and global_root_base and configured_root_base and global_root_base != configured_root_base),
        },
        "entitlement_snapshot": {
            "loaded": loaded,
            "path": str(path),
            "updated_at": _text(raw_snapshot.get("updated_at") or entitlement.get("effective_at")),
        },
    }


def compact_economic_status_for_control_report() -> dict[str, Any]:
    status = current_subnet_economic_status()
    usage = status.get("usage")
    usage_payload = usage if isinstance(usage, Mapping) else {}
    return {
        "schema": status["schema"],
        "generated_at": status["generated_at"],
        "source": status["source"],
        "subnet_id": status["subnet_id"],
        "zone_id": status["zone_id"],
        "subscription_state": status["subscription_state"],
        "plan_id": status["plan_id"],
        "entitlement_state": status["entitlement_state"],
        "enforcement_mode": status["enforcement_mode"],
        "enforcement_active": status["enforcement_active"],
        "disabled_resource_count": status["disabled_resource_count"],
        "disabled_resources": list(status.get("disabled_resources") or [])[:40],
        "usage": {"llm.requests": usage_payload.get("llm.requests", {})},
        "management_authority": status["management_authority"],
        "entitlement_snapshot": status["entitlement_snapshot"],
    }


__all__ = [
    "ECONOMIC_STATUS_SCHEMA",
    "ROOT_GOVERNED_RESOURCES",
    "compact_economic_status_for_control_report",
    "current_subnet_economic_status",
    "entitlement_snapshot_path",
]
