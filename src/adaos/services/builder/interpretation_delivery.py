"""Explicit interpretation transport packets; one canonical meaning contract.

Typed v3 carries source-clause IDs and exact atomic quotes as plain strings,
not source text enums. Core grounds each quote before full admission.
Old wire identities keep their original decoding; no historical rewrite.

The text-envelope profile constrains the outer JSON at the provider, not its
embedded proposal. Core still admits the full canonical proposal and source
evidence. It is an explicit compatibility experiment, never an automatic retry.
"""

from __future__ import annotations

import copy
import json
import os
import re

from adaos.services.builder_context import canonical
from .task_interpretation import (
    interpretation_contract,
    interpretation_messages,
    TaskInterpretationRejected,
)
from .task_spec import verify_identity

TYPED_NAME = "adaos_builder_task_interpretation"
TEXT_NAME = "adaos_builder_task_interpretation_json_text"
BOUND_TEXT_NAME = "adaos_builder_task_interpretation_bound_json_text_v1"
BOUND_TYPED_NAME = "adaos_builder_task_interpretation_bound_typed_v1"
CLAUSE_TYPED_NAME = "adaos_builder_task_interpretation_clause_refs_v2"
BOUND_CLAUSE_TYPED_NAME = "adaos_builder_task_interpretation_bound_clause_refs_v2"
QUOTE_TYPED_NAME = "adaos_builder_task_interpretation_clause_quotes_v3"
BOUND_QUOTE_TYPED_NAME = "adaos_builder_task_interpretation_bound_clause_quotes_v3"
CANONICAL_SCHEMA = "adaos.builder.task_interpretation.v1"
CORE_OWNED_FIELDS = frozenset({"schema", "source_digest"})


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Ambiguous duplicate JSON property")
        value[key] = item
    return value


def _source_binding(task):
    try:
        verify_identity(task, id_key="task_spec_id", prefix="task:")
        source_digest = task["source"]["digest"]
        if not isinstance(source_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", source_digest
        ):
            raise ValueError("Invalid frozen source identity")
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise TaskInterpretationRejected(
            [
                {
                    "code": "task.interpretation_source_binding_invalid",
                    "gate": "G5",
                    "owner": "context_compilation",
                    "detail": "The retained Core task cannot provide a valid interpretation source binding",
                }
            ]
        ) from error
    return {"schema": CANONICAL_SCHEMA, "source_digest": source_digest}


def _bound_contract(contract, task):
    """Remove only constants that Core owns before any model submission."""
    binding = _source_binding(task)
    result = copy.deepcopy(contract)
    for key, expected in binding.items():
        field = result.get("properties", {}).get(key) or {}
        fixed = field.get("const") == expected or field.get("enum") == [expected]
        conflicting = ("const" in field and field["const"] != expected) or (
            "enum" in field and field["enum"] != [expected]
        )
        supported_field = field.get("type") == "string" and not set(field) - {
            "type",
            "const",
            "enum",
            "description",
            "title",
        }
        if (
            not fixed
            or conflicting
            or not supported_field
            or key not in result.get("required", [])
        ):
            raise TaskInterpretationRejected(
                [
                    {
                        "code": "task.interpretation_binding_projection_invalid",
                        "gate": "G5",
                        "owner": "context_compilation",
                        "detail": "The source-bound transport cannot remove a nonconstant canonical field",
                    }
                ]
            )
        result["properties"].pop(key)
        result["required"].remove(key)
    return result


def request_packet(task, *, previous_brief=None, output_mode=None):
    mode = output_mode if output_mode is not None else os.getenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "typed")
    if mode not in {"typed", "json_text_envelope", "bound_json_text", "bound_typed"}:
        raise TaskInterpretationRejected(
            [
                {
                    "code": "task.interpretation_output_mode_unknown",
                    "gate": "G5",
                    "owner": "context_compilation",
                    "detail": "Unknown interpretation output transport profile",
                }
            ]
        )
    source_references = mode in {'typed', 'bound_typed'}
    contract = interpretation_contract(
        provider=True, task=task, previous_brief=previous_brief, source_references=source_references,
        source_quotes=source_references
    )
    messages = interpretation_messages(task, previous_brief=previous_brief, source_references=source_references,
                                       source_quotes=source_references)
    if mode == "typed":
        return {
            "mode": mode,
            "messages": messages,
            "wire_name": QUOTE_TYPED_NAME,
            "wire_schema": contract,
        }
    before = "Return only the typed proposal. "
    if messages[0]["content"].count(before) != 1:
        raise TaskInterpretationRejected(
            [
                {
                    "code": "task.interpretation_prompt_projection_invalid",
                    "gate": "G5",
                    "owner": "context_compilation",
                    "detail": "The transport projection cannot replace the owned output instruction exactly",
                }
            ]
        )
    if mode == "bound_typed":
        messages[0]["content"] = messages[0]["content"].replace(
            before,
            "Return only the typed semantic payload, omitting ROOT properties schema and source_digest. "
            "Identically named record fields inside facts remain ordinary source data and must be preserved. "
            "Core binds those constants from this exact frozen task before full canonical admission. ",
            1,
        )
        return {
            "mode": mode,
            "messages": messages,
            "wire_name": BOUND_QUOTE_TYPED_NAME,
            "wire_schema": _bound_contract(contract, task),
        }
    binding_instruction = (
        "The inner payload MUST OMIT schema and source_digest entirely. "
        "This prohibition concerns ROOT properties only; identically named record fields inside facts remain source data. "
        "Those are Core-owned constants, not semantic choices or strings to copy. "
        "Core binds the payload only to this frozen request's source before full canonical admission. "
        if mode == "bound_json_text"
        else ""
    )
    messages[0]["content"] = messages[0]["content"].replace(
        before,
        "Return only an object with exactly proposal_json, a string containing ONE complete JSON interpretation proposal. "
        + binding_instruction
        + "That inner proposal must satisfy interpretation_contract supplied beside the source evidence. "
        "The outer string is transport only: do not replace the proposal with prose, summaries, markdown or partial JSON. "
        "Core independently checks the entire inner contract, literal grounding and source ownership. ",
        1,
    )
    data = json.loads(messages[-1]["content"])
    # Source-bearing enum values remain in user evidence, not promoted into
    # higher-authority instructions or silently removed from model context.
    data["interpretation_contract"] = contract
    if mode == "bound_json_text":
        data["interpretation_contract"] = _bound_contract(contract, task)
    messages[-1]["content"] = canonical(data)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["proposal_json"],
        "properties": {"proposal_json": {"type": "string"}},
    }
    return {
        "mode": mode,
        "messages": messages,
        "wire_name": BOUND_TEXT_NAME if mode == "bound_json_text" else TEXT_NAME,
        "wire_schema": schema,
    }


def decode_proposal(record):
    """Decode the frozen transport name, never today's environment setting."""
    try:
        name = record["options"]["text"]["format"]["name"]
    except (KeyError, TypeError, AttributeError):
        name = None
    if not isinstance(name, str) or name not in (
        TYPED_NAME,
        TEXT_NAME,
        BOUND_TEXT_NAME,
        BOUND_TYPED_NAME,
        CLAUSE_TYPED_NAME,
        BOUND_CLAUSE_TYPED_NAME,
        QUOTE_TYPED_NAME,
        BOUND_QUOTE_TYPED_NAME,
    ):
        raise TaskInterpretationRejected(
            [
                {
                    "code": "task.interpretation_transport_unknown",
                    "gate": "G5",
                    "owner": "context_compilation",
                    "detail": "The retained interpretation transport is not supported; its format must not be guessed",
                }
            ]
        )
    # Context corruption is not model output and must retain its G5 owner.
    # TaskInterpretationRejected subclasses ValueError, so do not bind inside
    # the JSON decoding exception boundary below.
    source_quotes = name in {QUOTE_TYPED_NAME, BOUND_QUOTE_TYPED_NAME}
    source_references = source_quotes or name in {CLAUSE_TYPED_NAME, BOUND_CLAUSE_TYPED_NAME}
    bound = name in {BOUND_TEXT_NAME, BOUND_TYPED_NAME, BOUND_CLAUSE_TYPED_NAME, BOUND_QUOTE_TYPED_NAME}
    binding = (
        _source_binding(record.get("task_spec"))
        if bound or source_references
        else None
    )
    try:
        parser_options = (
            {"object_pairs_hook": _unique_object}
            if bound or source_references
            else {}
        )
        value = json.loads(record["result"].get("output_text") or "", **parser_options)
        if source_references:
            if not isinstance(value, dict) or not isinstance(value.get('requirements'), list):
                raise ValueError('Missing source-referenced requirements')
            clauses = {row['id']: row['text'].strip() for row in record['task_spec']['clauses']}
            for row in value['requirements']:
                if (not isinstance(row, dict) or 'statement' in row
                        or not isinstance(row.get('source_clause'), str)
                        or row['source_clause'] not in clauses
                        or not isinstance(row.get('clause_ids'), list)
                        or row['source_clause'] not in row['clause_ids']):
                    raise ValueError('A requirement must select its own exact source clause')
                source = clauses[row.pop('source_clause')]
                if source_quotes:
                    quote = row.pop('statement_text', None)
                    if (not isinstance(quote, str) or not quote.strip() or quote != quote.strip()
                            or quote not in source or source.find(quote, source.find(quote) + 1) != -1):
                        raise ValueError('A requirement quote must be exact and unambiguous in its source clause')
                    row['statement'] = quote
                else:
                    if 'statement_text' in row:
                        raise ValueError('A historical clause-reference payload cannot supply a quote')
                    row['statement'] = source
            if bound:
                if CORE_OWNED_FIELDS.intersection(value):
                    raise ValueError('Source-bound payload must not supply Core-owned fields')
                value = {**binding, **value}
            return value
        if name == TYPED_NAME:
            return value
        if name == BOUND_TYPED_NAME:
            if not isinstance(value, dict) or CORE_OWNED_FIELDS.intersection(value):
                raise ValueError(
                    "Source-bound typed payload must not supply Core-owned fields"
                )
            return {**binding, **value}
        if name in (TEXT_NAME, BOUND_TEXT_NAME):
            if (
                not isinstance(value, dict)
                or set(value) != {"proposal_json"}
                or not isinstance(value["proposal_json"], str)
            ):
                raise ValueError("Invalid text envelope")
            payload = json.loads(value["proposal_json"], **parser_options)
            if name == TEXT_NAME:
                return payload
            if not isinstance(payload, dict) or CORE_OWNED_FIELDS.intersection(payload):
                raise ValueError(
                    "Source-bound payload must not supply Core-owned fields"
                )
            return {**binding, **payload}
        raise ValueError("Unknown retained transport name")
    except (ValueError, TypeError) as error:
        raise TaskInterpretationRejected(
            [
                {
                    "code": "task.interpretation_response_invalid",
                    "gate": "G3",
                    "owner": "task_interpretation",
                    "detail": "The retained provider result is not a complete interpretation proposal",
                }
            ]
        ) from error
