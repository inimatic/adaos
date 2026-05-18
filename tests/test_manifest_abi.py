from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, Draft7Validator, ValidationError


def _load_schema(name: str) -> dict:
    path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _load_service_skill_schema() -> dict:
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "services"
        / "skill"
        / "skill_schema.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_skill_schema_accepts_runtime_activation_policy() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "infrascope_skill",
        "version": "0.9.0",
        "runtime": {
            "python": "3.11",
            "activation": {
                "mode": "lazy",
                "startup_allowed": False,
                "background_refresh": False,
                "when": {
                    "scenarios_active": ["infrascope"],
                    "client_presence": True,
                    "webspace_scope": "active",
                    "webspaces": ["default"],
                },
            },
        },
    }

    Draft7Validator(schema).validate(payload)


def test_skill_schema_accepts_browser_data_route_plan() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "infrastate_skill",
        "version": "0.76.0",
        "data_routes": [
            {
                "surface": "widget:runtime_status",
                "route": "yjs",
                "projection_slot": "infrastate.state",
                "first_paint": "compact cached badge",
                "recovery": "Yjs room replay restores latest compact status",
                "update_source": ["runtime.ready", "yjs.guard.changed"],
                "budget": {
                    "max_payload_bytes": 4096,
                    "max_publish_hz": 0.5,
                    "coalesce_ms": 1000,
                    "snapshot_policy": "on_subscribe",
                },
                "guard_visibility": {
                    "degraded_state": "status badge shows Yjs pressure",
                    "log": "quarantine.jsonl",
                    "quarantine": True,
                    "metric": "yjs_owner_guard.suppressed",
                },
            },
            {
                "surface": "modal:operations",
                "route": "stream",
                "receiver": "infrastate.operations.active",
                "first_paint": "empty active operations list",
                "recovery": "snapshot requested on stream subscribe",
                "update_source": "operation.changed",
            },
        ],
    }

    Draft7Validator(schema).validate(payload)


def test_skill_schema_rejects_unknown_browser_data_route() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "bad_skill",
        "version": "1.0.0",
        "data_routes": [
            {
                "surface": "widget:status",
                "route": "magic_runtime_autoroute",
            }
        ],
    }

    with pytest.raises(ValidationError):
        Draft7Validator(schema).validate(payload)


def test_runtime_skill_validator_schema_accepts_data_routes() -> None:
    schema = _load_service_skill_schema()
    payload = {
        "name": "route_aware_skill",
        "version": "0.1.0",
        "data_routes": [
            {
                "surface": "widget:status",
                "route": "stream",
                "receiver": "route_aware.status",
                "budget": {
                    "max_payload_bytes": 2048,
                    "max_publish_hz": 1,
                    "snapshot_policy": "on_subscribe_if_stale",
                },
                "guard_visibility": "show degraded status and log suppression count",
            }
        ],
    }

    Draft202012Validator(schema).validate(payload)


def test_skill_schema_rejects_unknown_activation_mode() -> None:
    schema = _load_schema("skill.schema.json")
    payload = {
        "name": "bad_skill",
        "version": "1.0.0",
        "runtime": {
            "activation": {
                "mode": "hot",
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft7Validator(schema).validate(payload)


def test_scenario_schema_accepts_runtime_skill_bindings() -> None:
    schema = _load_schema("scenario.schema.json")
    payload = {
        "id": "infrascope",
        "version": "0.6.0",
        "depends": ["legacy_skill"],
        "runtime": {
            "skills": {
                "required": ["infrascope_skill"],
                "optional": ["telemetry_skill"],
            }
        },
    }

    Draft7Validator(schema).validate(payload)


def test_scenario_schema_rejects_unknown_runtime_skill_field() -> None:
    schema = _load_schema("scenario.schema.json")
    payload = {
        "id": "infrascope",
        "version": "0.6.0",
        "runtime": {
            "skills": {
                "required": ["infrascope_skill"],
                "preferred": ["telemetry_skill"],
            }
        },
    }

    with pytest.raises(ValidationError):
        Draft7Validator(schema).validate(payload)
