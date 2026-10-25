"""Publisher adapters. Contract units use the same identity as usage locks."""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from .records import digest


class ChangeCollector:
    """Publisher output for CI/local builds that do not own runtime state."""

    def publish_change(self, **fields):
        from .records import seal
        fields = {key: value for key, value in fields.items()
                  if key not in {"before_descriptor", "after_descriptor"} or value is not None}
        return seal("adaos.contract_change.v1", recorded_at="1970-01-01T00:00:00+00:00", **{
            "dimensions": {}, "findings": [], "removal_after": None, "replacement_refs": [],
            "migrator_refs": [], "migration_route": "consumer_adaptation",
            "publisher_ref": "core:contract_publisher", **fields})


def sdk_units(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    value = payload.get("migration", payload)
    return {"sdk:" + item["name"]: dict(item) for item in value.get("tools", [])
            if item.get("name") and item.get("kind") in {"tool", "sdk_function"}}


def unit_digest(unit: Mapping[str, Any]) -> str:
    return str((unit.get("contract") or {}).get("digest") or digest(unit))


def ui_units(catalog: Mapping[str, Any], webui_schema: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    if webui_schema is None:
        webui_schema = json.loads((Path(__file__).resolve().parents[2] / "abi" / "webui.v1.schema.json").read_text(encoding="utf-8"))
    definitions = webui_schema.get("$defs", {})
    def fragments(reference):
        found = {}
        def visit(value):
            if isinstance(value, list):
                for child in value:
                    visit(child)
            elif isinstance(value, dict):
                ref = value.get("$ref", "")
                if isinstance(ref, str) and "#/$defs/" in ref:
                    name = ref.split("#/$defs/", 1)[1]
                    if name not in found and name in definitions:
                        found[name] = definitions[name]
                        visit(definitions[name])
                for child in value.values():
                    visit(child)
        visit({"$ref": reference})
        return found
    units = {}
    for item in catalog.get("components", []):
        if not item.get("id"):
            continue
        manifest = dict(item.get("manifest") or {})
        ref = "webui:" + str(manifest.get("widget_type") or item["id"])
        # Labels/aliases are discovery data; validation fragments and
        # materialization declarations determine compatibility impact.
        units[ref] = {"manifest": manifest,
                      "schema_fragments": fragments(manifest.get("input_schema", ""))}
    for item in catalog.get("authoring_lifecycle", {}).get("deprecated_interfaces", []):
        units["webui:" + item["interface"]] = dict(item)
    return units


@lru_cache(maxsize=1)
def current_contracts() -> dict[str, dict[str, Any]]:
    # This runs during trusted build/explicit scan, never on catalog reads.
    from adaos.sdk.core.exporter import export
    from adaos.services.root_mcp.applications_plane import contracts
    catalog = json.loads((Path(__file__).resolve().parents[2] / "abi" / "ui.capability_catalog.v1.json").read_text(encoding="utf-8"))
    units = {**sdk_units(export(level="rich", include_deprecated=True)), **ui_units(catalog)}
    for contract in contracts():
        # User-facing descriptions are part of authoring discovery but not
        # a claim that operation semantics have been verified.
        units["mcp:" + contract.id] = mcp_unit(contract.to_dict())
    return units


def publish_sdk_changes(service, previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[dict[str, Any]]:
    from adaos.sdk.core.exporter import compatibility_report
    before, after = sdk_units(previous), sdk_units(current)
    report = compatibility_report(dict(previous.get("migration", previous)), dict(current.get("migration", current)))
    results = []
    for ref in sorted(set(before) | set(after)):
        old, new = before.get(ref), after.get(ref)
        old_digest, new_digest = unit_digest(old) if old else None, unit_digest(new) if new else None
        if old_digest == new_digest:
            continue
        findings = [item for group in ("breaking", "additive", "changes") for item in report[group]
                    if "sdk:" + item["symbol"] == ref]
        breaking = [item for item in report["breaking"] if "sdk:" + item["symbol"] == ref]
        classification = "additive" if old is None else "breaking" if new is None or breaking else "potentially_breaking"
        if new and (new.get("contract") or {}).get("deprecated") and not breaking:
            classification = "deprecated"
        results.append(service.publish_change(contract_ref=ref, contract_kind="sdk_operation", owner_ref="core:sdk",
            before=old_digest, after=new_digest, classification=classification, findings=findings,
            before_descriptor=old, after_descriptor=new,
            dimensions={"source": "breaking" if breaking else "unknown"},
            required_evidence=[] if classification == "additive" else ["contract_test", "runtime_smoke"]))
    return results


def publish_ui_changes(service, previous: Mapping[str, Any], current: Mapping[str, Any], *,
                       previous_schema: Mapping[str, Any] | None = None,
                       current_schema: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    before, after = ui_units(previous, previous_schema), ui_units(current, current_schema)
    results = []
    for ref in sorted(set(before) | set(after)):
        old, new = before.get(ref), after.get(ref)
        if old == new:
            continue
        migrator = new.get("migrator") if new else None
        classification = "additive" if old is None else "breaking" if new is None else "deprecated" if migrator else "potentially_breaking"
        results.append(service.publish_change(contract_ref=ref, contract_kind="webui", owner_ref="core:client",
            before=digest(old) if old is not None else None, after=digest(new) if new is not None else None,
            classification=classification, required_evidence=["contract_test"],
            before_descriptor=old, after_descriptor=new,
            migration_route="declarative_projection" if migrator else "consumer_adaptation",
            migrator_refs=["migrator:" + migrator] if migrator else []))
    return results


def mcp_unit(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {key: contract[key] for key in ("id", "input_schema", "output_schema", "required_capability",
            "side_effects", "stability", "availability", "metadata") if key in contract}


def publish_mcp_changes(service, previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[dict[str, Any]]:
    from adaos.sdk.core.exporter import compatibility_report
    before, after = {key: mcp_unit(value) for key, value in previous.items()}, {key: mcp_unit(value) for key, value in current.items()}
    results = []
    for identity in sorted(set(before) | set(after)):
        old, new = before.get(identity), after.get(identity)
        if old == new:
            continue
        def exported(value):
            return {"tools": [{"name": identity, **value, "contract": {
                "permissions": [value["required_capability"]] if value.get("required_capability") else [],
                "digest": digest(value)}}]} if value else {"tools": []}
        report = compatibility_report(exported(old), exported(new))
        classification = "additive" if old is None else "breaking" if new is None or report["breaking"] else "potentially_breaking"
        results.append(service.publish_change(contract_ref="mcp:" + identity, contract_kind="api_operation",
            owner_ref="core:api", before=digest(old) if old else None, after=digest(new) if new else None,
            before_descriptor=old, after_descriptor=new,
            classification=classification, findings=report["breaking"], required_evidence=["contract_test", "runtime_smoke"]))
    return results
