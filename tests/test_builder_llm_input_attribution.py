from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from adaos.services.builder.llm_input_attribution import (
    build_llm_input_attribution,
)


def test_llm_input_attribution_is_content_addressed_and_schema_valid() -> None:
    messages = [
        {"role": "system", "content": "Policy"},
        {"role": "user", "content": '{"stable":true}'},
        {"role": "user", "content": '{"request":"Create a board"}'},
    ]
    selection = {
        "catalog_digest": "sha256:catalog",
        "items": [
            {"id": "collection.board", "kind": "component", "summary": "Board"},
            {"id": "layout.split", "kind": "layout", "summary": "Split"},
            {
                "id": "recipe.resource_board_workbench",
                "kind": "recipe",
                "examples": [{"purpose": "create"}],
            },
        ],
        "input_attribution": {"profile": "generic", "domain_packs": []},
    }

    receipt = build_llm_input_attribution(
        request_id="request-1",
        route="prototype.transform",
        stage="generate",
        attempt=1,
        messages=messages,
        message_purposes=("system_policy", "stable_context", "user_delta"),
        capability_selection=selection,
        model="gpt-5",
        generation_options={
            "temperature": 0.25,
            "max_tokens": 1000,
            "api_key": "must-not-leak",
        },
        created_at="2026-09-10T10:00:00+00:00",
    )

    schema_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "abi"
        / "builder.llm_input_attribution.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(receipt)
    assert receipt["profile"] == "generic"
    assert [item["id"] for item in receipt["capabilities"]["component_contracts"]] == [
        "collection.board"
    ]
    assert [item["id"] for item in receipt["capabilities"]["generic_patterns"]] == [
        "layout.split",
        "recipe.resource_board_workbench",
    ]
    assert receipt["capabilities"]["examples"][0]["kind"] == "example"
    assert receipt["capabilities"]["domain_packs"] == []
    assert "api_key" not in receipt["generation"]["options"]
    assert receipt["stable_prefix"]["message_indexes"] == [0, 1]
    assert receipt["dynamic_suffix"]["message_indexes"] == [2]


def test_llm_input_attribution_changes_only_dynamic_digest_for_user_delta() -> None:
    base = [
        {"role": "system", "content": "Policy"},
        {"role": "user", "content": "Stable contracts"},
        {"role": "user", "content": "First request"},
    ]
    kwargs = {
        "request_id": "request-1",
        "route": "prototype.transform",
        "stage": "generate",
        "attempt": 1,
        "message_purposes": ("system_policy", "stable_context", "user_delta"),
        "capability_selection": {},
        "created_at": "2026-09-10T10:00:00+00:00",
    }
    first = build_llm_input_attribution(messages=base, **kwargs)
    changed = [*base[:2], {"role": "user", "content": "Second request"}]
    second = build_llm_input_attribution(messages=changed, **kwargs)

    assert first["stable_prefix"]["sha256"] == second["stable_prefix"]["sha256"]
    assert first["dynamic_suffix"]["sha256"] != second["dynamic_suffix"]["sha256"]


def test_capability_context_is_part_of_the_cacheable_prefix() -> None:
    receipt = build_llm_input_attribution(
        request_id="request-capabilities",
        route="prototype.transform",
        stage="generate",
        attempt=1,
        messages=[
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "contract"},
            {"role": "user", "content": "capabilities"},
            {"role": "user", "content": "task"},
        ],
        message_purposes=(
            "system_policy",
            "stable_context",
            "capability_context",
            "user_delta",
        ),
    )

    assert receipt["stable_prefix"]["message_indexes"] == [0, 1, 2]
    assert receipt["dynamic_suffix"]["message_indexes"] == [3]
