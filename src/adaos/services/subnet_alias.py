from __future__ import annotations

import re
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from adaos.adapters.db.sqlite import durable_state_delete, durable_state_get, durable_state_put
from adaos.services.runtime_paths import current_base_dir


_GENERIC_HUB_ALIAS_RE = re.compile(r"^hub(?:-\d+)?$", re.IGNORECASE)
_ALIAS_NAMESPACE = "subnet_alias"
_ALIAS_KEY = "local"


def display_subnet_alias(alias: str | None, subnet_id: str | None) -> str | None:
    raw_alias = str(alias or "").strip()
    raw_subnet = str(subnet_id or "").strip()
    if raw_alias and not _GENERIC_HUB_ALIAS_RE.fullmatch(raw_alias):
        return raw_alias
    if raw_subnet:
        return raw_subnet
    return raw_alias or None


def _clear_legacy_alias_from_node_yaml() -> None:
    try:
        from adaos.services.capacity import _load_node_yaml, _save_node_yaml
    except Exception:
        return
    try:
        payload = _load_node_yaml()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return
    nats = payload.get("nats")
    if not isinstance(nats, dict) or "alias" not in nats:
        return
    next_nats = dict(nats)
    next_nats.pop("alias", None)
    next_payload = dict(payload)
    if next_nats:
        next_payload["nats"] = next_nats
    else:
        next_payload.pop("nats", None)
    _save_node_yaml(next_payload)


def _node_yaml_path() -> Path:
    return current_base_dir() / "node.yaml"


def _load_node_yaml_payload() -> dict[str, Any]:
    path = _node_yaml_path()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_node_yaml_payload(payload: dict[str, Any]) -> None:
    path = _node_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    try:
        tmp_path.replace(path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def _read_subnet_names_from_node_yaml(payload: dict[str, Any]) -> tuple[str, str | None]:
    subnet = payload.get("subnet") if isinstance(payload.get("subnet"), dict) else {}
    names = subnet.get("names") if isinstance(subnet, dict) else None
    if isinstance(names, str):
        raw_names = [item.strip() for item in names.replace("\n", ",").split(",")]
    elif isinstance(names, (list, tuple, set)):
        raw_names = [str(item or "").strip() for item in names]
    else:
        raw_names = []
    alias = next((item for item in raw_names if item), "")
    subnet_id = str(
        (subnet.get("id") if isinstance(subnet, dict) else "")
        or payload.get("subnet_id")
        or ""
    ).strip() or None
    return alias, subnet_id


def _read_legacy_alias_from_node_yaml(payload: dict[str, Any], *, subnet_id: str | None = None) -> tuple[str | None, str | None]:
    current_subnet_id = str(subnet_id or "").strip()
    nats = payload.get("nats")
    if not isinstance(nats, dict):
        return None, None
    legacy_alias = str(nats.get("alias") or "").strip()
    if not legacy_alias:
        return None, None
    legacy_subnet_id = str(
        payload.get("subnet_id")
        or ((payload.get("subnet") or {}).get("id") if isinstance(payload.get("subnet"), dict) else "")
        or ""
    ).strip() or None
    if current_subnet_id and legacy_subnet_id and current_subnet_id != legacy_subnet_id:
        return None, legacy_subnet_id
    return legacy_alias, legacy_subnet_id


def _persist_alias_to_node_yaml(alias: str | None, *, subnet_id: str | None = None) -> None:
    payload = _load_node_yaml_payload()
    subnet = dict(payload.get("subnet") or {}) if isinstance(payload.get("subnet"), dict) else {}
    current_subnet_id = str(subnet_id or subnet.get("id") or payload.get("subnet_id") or "").strip()
    if current_subnet_id:
        payload["subnet_id"] = current_subnet_id
        subnet["id"] = current_subnet_id
    token = str(alias or "").strip()
    if token:
        subnet["names"] = [token]
    else:
        subnet.pop("names", None)
    if subnet:
        payload["subnet"] = subnet
    elif "subnet" in payload:
        payload.pop("subnet", None)
    _write_node_yaml_payload(payload)


def save_subnet_alias(alias: str | None, *, subnet_id: str | None = None) -> str | None:
    token = str(alias or "").strip()
    if token:
        durable_state_put(
            _ALIAS_NAMESPACE,
            _ALIAS_KEY,
            {
                "alias": token,
                "subnet_id": str(subnet_id or "").strip() or None,
                "updated_at": time.time(),
            },
        )
    else:
        durable_state_delete(_ALIAS_NAMESPACE, _ALIAS_KEY)
    _persist_alias_to_node_yaml(token or None, subnet_id=subnet_id)
    _clear_legacy_alias_from_node_yaml()
    return token or None


def load_subnet_alias(*, subnet_id: str | None = None) -> str | None:
    node_payload = _load_node_yaml_payload()
    node_yaml_alias, node_yaml_subnet_id = _read_subnet_names_from_node_yaml(node_payload)
    current_subnet_id = str(subnet_id or "").strip()
    if node_yaml_alias and (not current_subnet_id or not node_yaml_subnet_id or node_yaml_subnet_id == current_subnet_id):
        return node_yaml_alias

    payload = durable_state_get(_ALIAS_NAMESPACE, _ALIAS_KEY) or {}
    if isinstance(payload, dict):
        alias = str(payload.get("alias") or "").strip()
        stored_subnet_id = str(payload.get("subnet_id") or "").strip()
        if alias and (not current_subnet_id or not stored_subnet_id or stored_subnet_id == current_subnet_id):
            return alias

    alias, legacy_subnet_id = _read_legacy_alias_from_node_yaml(node_payload, subnet_id=subnet_id)
    if not alias:
        return None
    save_subnet_alias(alias, subnet_id=legacy_subnet_id)
    if current_subnet_id and legacy_subnet_id and current_subnet_id != legacy_subnet_id:
        return None
    return alias

