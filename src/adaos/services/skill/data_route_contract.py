"""Bounded manifest ABI closure for stream/data-route authoring."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def automation_stream_contract() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    schema_bytes = (root / "abi" / "skill.schema.json").read_bytes()
    manifest = json.loads(schema_bytes)
    route = manifest["properties"]["data_routes"]
    definitions: dict[str, Any] = {}
    pending = [route]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            ref = str(value.get("$ref") or "")
            if ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                if name not in definitions:
                    definitions[name] = manifest["$defs"][name]
                    pending.append(definitions[name])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    schema = {
        "$schema": manifest.get("$schema", "https://json-schema.org/draft/2020-12/schema"),
        "type": "object", "required": ["data_routes"],
        "properties": {"data_routes": route}, "$defs": definitions,
    }
    activation_bytes = (root / "services" / "skill" / "activation.py").read_bytes()
    contract = {
        "schema": "adaos.builder.skill_stream_contract.v1",
        "manifest_field": "skill.yaml:data_routes",
        "json_schema": schema,
        "provenance": {
            "manifest_schema": "adaos/abi/skill.schema.json",
            "manifest_schema_digest": "sha256:" + hashlib.sha256(schema_bytes).hexdigest(),
            "admission_source": "adaos/services/skill/activation.py",
            "admission_source_digest": "sha256:" + hashlib.sha256(activation_bytes).hexdigest(),
        },
        "receiver_policy": {
            "declaration": "There is no separate receiver_policy manifest field. route=stream contributes receiver; route=yjs contributes projection_slot. Skill webui dataSource receiver declarations also contribute patterns.",
            "matching": "Exact receiver names are preferred. '*' uses case-sensitive fnmatch; for dynamic '$' templates, each dot-separated segment starting with '$' is replaced by '*' before fnmatch. Never declare foreign receiver ownership to silence diagnostics.",
            "admission": "For webio.stream.snapshot.requested, webio.stream.subscription.changed, webio.yjs.snapshot.requested and webio.yjs.subscription.changed, a nonempty declared pattern set denies unmatched receivers BEFORE handler dispatch. An empty set is a legacy compatibility allowance with stream_receiver_policy_missing diagnostics, not isolation.",
            "handler_lifecycle": "Ignore inactive subscriptions; stop and join producer work on unsubscribe/dispose; coalesce unchanged samples. Manifest budgets document bounds; implement and test the corresponding producer limits, do not assume declaration alone schedules or throttles work.",
            "validation": "Strict skill.yaml validation plus intended/foreign receiver admission and repeated subscribe/unsubscribe tests. Do not import private Core APIs in application runtime code; Core/origin owns admission enforcement and verification.",
        },
        "example": {"data_routes": [{
            "surface": "widget:status", "route": "stream", "owner": "sample_skill",
            "receiver": "sample.status", "first_paint": "bounded cached snapshot or loading",
            "recovery": "refresh snapshot on subscribe", "update_source": "status.changed",
            "budget": {"max_payload_bytes": 16384, "max_publish_hz": 1, "max_items": 50,
                       "coalesce_ms": 250, "snapshot_policy": "on_subscribe"},
            "guard_visibility": "visible unavailable state and bounded diagnostic reason",
        }]},
    }
    contract["digest"] = "sha256:" + hashlib.sha256(json.dumps(
        contract, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    return contract
