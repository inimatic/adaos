from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.domain import ApplicationSetupContract
from adaos.services.applications import (
    ApplicationSetupConflict,
    ApplicationSetupStateStore,
    compile_setup_contract,
    project_setup_state,
)


DIGEST = "sha256:" + "4" * 64


def _contract() -> ApplicationSetupContract:
    return compile_setup_contract(
        application_id="weather",
        release_digest=DIGEST,
        component_manifests={
            "skill:weather_skill": {
                "configuration": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "default_city": {"type": "string"},
                            "forecast_days": {"type": "integer"},
                        },
                        "required": ["default_city", "forecast_days"],
                        "additionalProperties": False,
                    },
                    "defaults": {"forecast_days": 5},
                    "credentials": {
                        "weather_api_token": {
                            "title": "Weather API token",
                            "purpose": "Use the optional commercial weather provider quota.",
                            "required": False,
                        }
                    },
                }
            }
        },
        permission_profile={
            "required": [
                {
                    "id": "network.egress",
                    "title": "Weather provider",
                    "purpose": "Request forecast data.",
                }
            ],
            "optional": [],
        },
        placement_required=True,
        verification=[{"id": "weather_probe", "title": "Weather provider probe"}],
    )


def test_setup_contract_compiles_release_owned_fields_without_secret_values() -> None:
    contract = _contract()
    payload = contract.to_dict()
    assert payload["application_id"] == "weather"
    assert payload["components"][0]["credentials"] == [
        {
            "slot": "weather_api_token",
            "title": "Weather API token",
            "purpose": "Use the optional commercial weather provider quota.",
            "required": False,
        }
    ]
    serialized = json.dumps(payload).lower()
    assert "credential:" not in serialized
    assert "secret_value" not in serialized
    assert contract.digest.startswith("sha256:")


def test_setup_projection_blocks_required_inputs_but_not_optional_weather_token() -> None:
    state = project_setup_state(
        _contract(),
        channel="stable",
        configuration={"skill:weather_skill": {}},
        permission_status={"network.egress": "ready"},
        placement_status="ready",
        verification_status={"weather_probe": "pending"},
    )
    assert state["status"] == "action_required"
    missing = {item["requirement_id"]: item for item in state["requirements"]}
    assert missing["setting:skill:weather_skill:default_city"]["status"] == "missing"
    assert missing["credential:skill:weather_skill:weather_api_token"]["required"] is False
    assert state["summary"]["optional_missing_total"] == 1

    validating = project_setup_state(
        _contract(),
        channel="stable",
        configuration={"skill:weather_skill": {"default_city": "Moscow"}},
        permission_status={"network.egress": "ready"},
        placement_status="ready",
        verification_status={"weather_probe": "pending"},
    )
    assert validating["status"] == "validating"

    ready = project_setup_state(
        _contract(),
        channel="stable",
        configuration={"skill:weather_skill": {"default_city": "Moscow"}},
        permission_status={"network.egress": "ready"},
        placement_status="ready",
        verification_status={"weather_probe": "ready"},
    )
    assert ready["status"] == "ready"
    assert ready["summary"] == {
        "required_total": 5,
        "ready_total": 5,
        "action_required_total": 0,
        "optional_missing_total": 1,
    }


def test_setup_state_store_is_revisioned_idempotent_and_schema_valid(tmp_path: Path) -> None:
    projection = project_setup_state(
        _contract(),
        channel="beta",
        configuration={"skill:weather_skill": {"default_city": "Moscow"}},
        permission_status={"network.egress": "ready"},
        placement_status="ready",
        verification_status={"weather_probe": "ready"},
    )
    store = ApplicationSetupStateStore(tmp_path)
    first = store.reconcile(projection, expected_revision=0)
    assert first["revision"] == 1
    assert store.reconcile(projection, expected_revision=1) == first
    with pytest.raises(ApplicationSetupConflict, match="expected revision 0, observed 1"):
        store.reconcile(projection, expected_revision=0)

    schema = json.loads(
        (Path(__file__).parents[1] / "src/adaos/abi/application.setup_state.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(first)


def test_runtime_and_canonical_skill_schemas_share_setup_slot_contract() -> None:
    root = Path(__file__).parents[1] / "src/adaos"
    canonical = json.loads((root / "abi/skill.schema.json").read_text(encoding="utf-8"))
    runtime = json.loads((root / "services/skill/skill_schema.json").read_text(encoding="utf-8"))
    canonical_slot = canonical["properties"]["configuration"]["properties"]["credentials"]["additionalProperties"]
    runtime_slot = runtime["properties"]["configuration"]["properties"]["credentials"]["additionalProperties"]
    assert runtime_slot == canonical_slot
