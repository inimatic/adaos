from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from adaos.services.bootstrap import load_config
from adaos.services.runtime_paths import current_base_dir
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
