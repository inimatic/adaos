from __future__ import annotations

import pytest

from adaos.sdk.builder.prototype import start_data_runtime
from adaos.services.builder.workflow import BuilderWorkflowError


def _definition(*, mode: str = "local_crud") -> dict:
    record_schema = {
        "type": "object",
        "required": ["id", "title", "done"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "title": {"type": "string", "minLength": 1},
            "done": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    object_output = record_schema
    return {
        "schema": "adaos.builder.prototype_data.v1",
        "source_id": "shopping.items",
        "mode": mode,
        "record_schema": record_schema,
        "seed": [{"id": "milk", "title": "Milk", "done": False}],
        "fixtures": {
            "provider.search": {
                "result": {"items": ["Milk", "Bread"]},
                "provenance": "recording:shop-search:2026-08-06",
            }
        },
        "activities": [
            {
                "activity_id": "shopping.list",
                "operation": "list",
                "input_schema": {"type": "object", "additionalProperties": False},
                "output_schema": {"type": "array", "items": record_schema},
                "side_effect_class": "read_only",
                "implementation_status": "mapped",
                "implementation_ref": "skill:shopping.list",
            },
            {
                "activity_id": "shopping.create",
                "operation": "create",
                "input_schema": {
                    "type": "object",
                    "required": ["record"],
                    "properties": {"record": record_schema},
                    "additionalProperties": False,
                },
                "output_schema": object_output,
                "side_effect_class": "local_reversible",
                "implementation_status": "missing",
                "implementation_ref": None,
            },
            {
                "activity_id": "shop.search",
                "operation": "fixture",
                "fixture_key": "provider.search",
                "input_schema": {"type": "object"},
                "output_schema": {
                    "type": "object",
                    "required": ["items"],
                    "properties": {"items": {"type": "array", "items": {"type": "string"}}},
                },
                "side_effect_class": "read_only",
                "implementation_status": "prototype_only",
                "implementation_ref": None,
            },
            {
                "activity_id": "shopping.suggest",
                "operation": "generate",
                "input_schema": {"type": "object"},
                "output_schema": {
                    "type": "object",
                    "required": ["text", "fixture"],
                    "properties": {"text": {"type": "string"}, "fixture": {"const": True}},
                },
                "side_effect_class": "read_only",
                "implementation_status": "prototype_only",
                "implementation_ref": None,
            },
        ],
    }


def test_disposable_crud_and_trace_are_deterministic() -> None:
    runtime = start_data_runtime(_definition())
    created = runtime.execute(
        "shopping.create",
        {"record": {"id": "bread", "title": "Bread", "done": False}},
        expected_generation=0,
    )
    listed = runtime.execute("shopping.list", {}, expected_generation=1)

    assert created["generation"] == 1
    assert [item["id"] for item in listed["result"]] == ["milk", "bread"]
    assert runtime.trace()["entries"][0]["activity_id"] == "shopping.create"
    assert runtime.snapshot()["generation"] == 1


def test_recorded_and_generated_results_have_explicit_provenance() -> None:
    runtime = start_data_runtime(_definition())
    fixture = runtime.execute("shop.search", {"query": "milk"})
    first = runtime.execute("shopping.suggest", {"for": "dinner"})
    second = start_data_runtime(_definition()).execute("shopping.suggest", {"for": "dinner"})

    assert fixture["trace_entry"]["provenance"] == {
        "kind": "recorded_fixture",
        "source_ref": "recording:shop-search:2026-08-06",
    }
    assert first["result"] == second["result"]
    assert first["result"]["fixture"] is True


def test_runtime_fails_on_stale_generation_and_undeclared_activity() -> None:
    runtime = start_data_runtime(_definition())
    with pytest.raises(BuilderWorkflowError, match="stale prototype generation"):
        runtime.execute("shopping.list", {}, expected_generation=2)
    with pytest.raises(BuilderWorkflowError, match="not declared"):
        runtime.execute("shopping.pay", {})


def test_static_mode_rejects_mutation() -> None:
    runtime = start_data_runtime(_definition(mode="static"))
    with pytest.raises(BuilderWorkflowError, match="read-only"):
        runtime.execute(
            "shopping.create",
            {"record": {"id": "bread", "title": "Bread", "done": False}},
        )


def test_activity_requirements_preserve_missing_implementation() -> None:
    requirements = start_data_runtime(_definition()).activity_requirements()
    create = next(item for item in requirements if item["activity_id"] == "shopping.create")
    assert create["implementation_status"] == "missing"
    assert create["implementation_ref"] is None
