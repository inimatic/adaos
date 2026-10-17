from __future__ import annotations

import json
from pathlib import Path

import pytest
from adaos.services.builder_context import ContextBudgetExceeded

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
            {
                "id": "layout.collection-detail",
                "kind": "layout",
                "summary": "Collection detail",
            },
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
        "layout.collection-detail",
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


def test_output_and_tool_schemas_participate_in_request_estimate_without_secret_payload():
    receipt = build_llm_input_attribution(
        request_id="schemas", route="test", stage="generate", attempt=1,
        messages=[{"role": "user", "content": "hello"}], message_purposes=["task"],
        generation_options={"response_format": {"type": "json_schema", "schema": {"type": "object"}},
                            "tools": [{"type": "mcp", "authorization": "must-not-log"}]},
    )
    configuration = receipt["generation"]["context_configuration"]
    assert configuration["keys"] == ["response_format", "tools"]
    assert configuration["estimated_tokens"] > 0
    assert receipt["totals"]["request_estimated_tokens"] > receipt["totals"]["estimated_tokens"]
    assert "must-not-log" not in json.dumps(receipt)
    assert receipt["final_input_budget"]["must_keep_complete"]


def test_provider_schema_can_block_before_builder_submits_a_request():
    with pytest.raises(ContextBudgetExceeded):
        build_llm_input_attribution(
            request_id="large-schema", route="prototype.transform", stage="generate", attempt=1,
            messages=[{"role": "user", "content": "small brief"}], message_purposes=["task"],
            generation_options={"response_format": {"schema": {"description": "x" * 800}}},
            context_capacity_tokens=160, context_reserve_tokens=20,
        )


def test_multimodal_bytes_do_not_masquerade_as_text_tokens_or_full_budget_coverage():
    messages = [{"role": "user", "content": [
        {"type": "input_text", "text": "Describe this image"},
        {"type": "input_image", "image_url": "data:image/png;base64," + "A" * 300_000},
    ]}]
    before = json.dumps(messages)
    receipt = build_llm_input_attribution(
        request_id="image", route="test", stage="generate", attempt=1,
        messages=messages, message_purposes=["task"],
        context_capacity_tokens=200, context_reserve_tokens=20,
    )
    budget = receipt["final_input_budget"]
    assert budget["status"] == "requires_modality_estimate"
    assert not budget["full_input_covered"]
    assert budget["unestimated_content_parts"] == 1
    assert receipt["totals"]["request_estimated_tokens"] < 180
    assert receipt["totals"]["request_bytes"] > 300_000
    assert json.dumps(messages) == before
    schema = json.loads((Path(__file__).resolve().parents[1] / "src/adaos/abi/builder.llm_input_attribution.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(receipt)


def test_known_text_overflow_still_blocks_when_an_image_is_present():
    with pytest.raises(ContextBudgetExceeded) as blocked:
        build_llm_input_attribution(
            request_id="large-text-image", route="test", stage="generate", attempt=1,
            messages=[{"role": "user", "content": [
                {"type": "input_text", "text": "x" * 1000},
                {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
            ]}], message_purposes=["task"],
            context_capacity_tokens=160, context_reserve_tokens=20,
        )
    assert blocked.value.receipt["status"] == "blocked"
    assert not blocked.value.receipt["full_input_covered"]


def test_parent_evidence_read_verifies_acknowledgement_and_bytes(tmp_path):
    from adaos.services.builder.llm_input_attribution import retain_llm_evidence, read_llm_evidence
    from adaos.services.context_control import ContextControlService
    evidence = {'request_id': 'primary', 'messages': [{'role': 'user', 'content': 'Exact input'}]}
    receipt = retain_llm_evidence(evidence, state_dir=tmp_path)
    assert read_llm_evidence(receipt, state_dir=tmp_path) == evidence
    with pytest.raises(ValueError, match='identity mismatch'):
        read_llm_evidence({**receipt, 'digest': 'sha256:' + '0' * 64}, state_dir=tmp_path)
    service = ContextControlService(state_dir=tmp_path)
    (service.artifact_root / (receipt['digest'][7:] + '.json')).write_text('{}', encoding='utf-8')
    with pytest.raises(Exception, match='digest mismatch'):
        read_llm_evidence(receipt, state_dir=tmp_path)
