from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from adaos.services.prompt_rules import (
    context_capsule_request,
    load_prompt_rule_registry,
    select_prompt_rules,
)
from adaos.services.context_control import ContextControlService


def test_prompt_rule_registry_is_versioned_and_selects_by_facets() -> None:
    registry = load_prompt_rule_registry()
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "src"
            / "adaos"
            / "abi"
            / "builder.prompt_rule_registry.v1.schema.json"
        ).read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(
        {key: value for key, value in registry.items() if key != "digest"}
    )
    assert registry["schema"] == "adaos.builder.prompt_rule_registry.v1"
    assert registry["version"] == "0.4.2"
    assert registry["digest"].startswith("sha256:")
    assert len({item["id"] for item in registry["items"]}) == len(registry["items"])
    assert [
        item["id"]
        for item in select_prompt_rules(
            target_type="skill",
            evidence="webui.json uses response_job for a subnet member status",
        )
    ] == [
        "adaos.builder.execution_boundary.v1",
        "adaos.skill.sdk_boundary.v1",
        "adaos.skill.webui_tool_contract.v2",
        "adaos.skill.async_llm_job.v1",
        "adaos.skill.member_subnet.v1",
    ]


def test_prompt_rule_registry_uses_structured_task_facts() -> None:
    ids = [
        item["id"]
        for item in select_prompt_rules(
            target_type="skill",
            evidence="rename the visible control",
            facts={
                "profile": "resource_crud",
                "target_files": ["skills/demo/handlers/main.py", "skills/demo/webui.json"],
                "target_refs": ["resource:demo.note"],
            },
        )
    ]

    assert ids[:4] == [
        "adaos.builder.execution_boundary.v1",
        "adaos.skill.sdk_boundary.v1",
        "adaos.skill.webui_tool_contract.v2",
        "adaos.ui.declarative_state.v1",
    ]
    assert "adaos.skill.resource_storage.v1" in ids


def test_scenario_always_gets_composition_boundary() -> None:
    ids = [
        item["id"]
        for item in select_prompt_rules(
            target_type="scenario",
            evidence="rename a title",
        )
    ]

    assert ids == [
        "adaos.builder.execution_boundary.v1",
        "adaos.skill.sdk_boundary.v1",
        "adaos.scenario.composition_boundary.v1",
    ]


def test_structured_facts_select_rules_without_english_text_markers() -> None:
    ids = [
        item["id"]
        for item in select_prompt_rules(
            target_type="skill",
            evidence="Нужно корректно переживать повторный запуск после обновления.",
            facts={
                "concepts": ["validation"],
                "surface_kinds": ["background"],
                "operation_kinds": ["validate"],
                "data_planes": [],
                "effects": ["publication"],
                "requires_lifecycle": True,
            },
        )
    ]

    assert "adaos.skill.runtime_lifecycle.v1" in ids
    assert "adaos.skill.python_lifecycle.v1" in ids
    assert "adaos.ui.declarative_state.v1" not in ids


def test_prompt_rule_capsule_registration_is_idempotent_and_searchable(
    tmp_path,
) -> None:
    contexts = ContextControlService(tmp_path)
    rule = next(
        item
        for item in select_prompt_rules(
            target_type="skill",
            evidence="webui.json",
        )
        if item["id"] == "adaos.skill.webui_tool_contract.v2"
    )

    first = contexts.register_capsule(context_capsule_request(rule))
    second = contexts.register_capsule(context_capsule_request(rule))

    assert second["capsule_id"] == first["capsule_id"]
    assert second["digest"] == first["digest"]
    assert contexts.list_capsules(search="skill.yaml exports.tools", limit=1) == [first]
