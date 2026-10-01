from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


_CATALOG_PATH = Path(__file__).resolve().parents[1] / "abi" / "ui.capability_catalog.v1.json"


def deprecation_contract() -> dict[str, Any]:
    catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    lifecycle = catalog.get("authoring_lifecycle")
    return copy.deepcopy(dict(lifecycle)) if isinstance(lifecycle, Mapping) else {"deprecated_interfaces": []}


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, Mapping[str, Any]]]:
    if isinstance(value, Mapping):
        yield path, value
        for key, child in value.items():
            yield from _walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def scan_deprecated_interfaces(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for rule in deprecation_contract().get("deprecated_interfaces") or []:
        if not isinstance(rule, Mapping) or rule.get("authoring") not in {"migration_only", "forbidden"}:
            continue
        detector = rule.get("detector") if isinstance(rule.get("detector"), Mapping) else {}
        field = str(detector.get("field") or "").strip()
        if not field:
            continue
        expected = detector.get("equals")
        for path, node in _walk(payload):
            if node.get(field) == expected:
                findings.append(
                    {
                        "code": "interface.deprecated",
                        "path": f"{path}.{field}",
                        "interface": rule.get("interface"),
                        "replacement": rule.get("replacement"),
                        "authoring": rule.get("authoring"),
                        "migrator": rule.get("migrator"),
                    }
                )
    return findings


def _migrate_open_modal(payload: Mapping[str, Any], owner: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from adaos.apps.open_modal_migrate import migrate_webui_payload

    return migrate_webui_payload(payload, owner=owner)


_MIGRATORS: dict[str, Callable[[Mapping[str, Any], str], tuple[dict[str, Any], dict[str, Any]]]] = {
    "open_modal_to_public_view": _migrate_open_modal,
}


def migrate_deprecated_interfaces(
    payload: Mapping[str, Any], *, owner: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = copy.deepcopy(dict(payload))
    applied: list[dict[str, Any]] = []
    for rule in deprecation_contract().get("deprecated_interfaces") or []:
        if not isinstance(rule, Mapping) or rule.get("authoring") != "migration_only":
            continue
        migration_id = str(rule.get("migrator") or "").strip()
        migrator = _MIGRATORS.get(migration_id)
        if migrator is None:
            continue
        before = scan_deprecated_interfaces(current)
        if not any(item.get("interface") == rule.get("interface") for item in before):
            continue
        current, receipt = migrator(current, owner)
        applied.append({"interface": rule.get("interface"), "migrator": migration_id, "receipt": receipt})
    remaining = scan_deprecated_interfaces(current)
    return current, {
        "schema": "adaos.interface_deprecation_migration.v1",
        "applied": applied,
        "remaining": remaining,
        "ok": not remaining,
    }


def validate_authoring_interfaces(payload: Mapping[str, Any]) -> dict[str, Any]:
    findings = scan_deprecated_interfaces(payload)
    return {"ok": not findings, "schema": "adaos.interface_deprecation_admission.v1", "findings": findings}
