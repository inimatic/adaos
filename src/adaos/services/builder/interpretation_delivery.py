"""Explicit interpretation transport packets; one canonical meaning contract.

The text-envelope profile constrains the outer JSON at the provider, not its
embedded proposal. Core still admits the full canonical proposal and source
evidence. It is an explicit compatibility experiment, never an automatic retry.
"""

from __future__ import annotations

import json
import os

from adaos.services.builder_context import canonical
from .task_interpretation import (
    interpretation_contract,
    interpretation_messages,
    TaskInterpretationRejected,
)

TYPED_NAME = "adaos_builder_task_interpretation"
TEXT_NAME = "adaos_builder_task_interpretation_json_text"


def request_packet(task, *, previous_brief=None):
    mode = os.getenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "typed")
    if mode not in {"typed", "json_text_envelope"}:
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
    contract = interpretation_contract(
        provider=True, task=task, previous_brief=previous_brief
    )
    messages = interpretation_messages(task, previous_brief=previous_brief)
    if mode == "typed":
        return {
            "mode": mode,
            "messages": messages,
            "wire_name": TYPED_NAME,
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
    messages[0]["content"] = messages[0]["content"].replace(
        before,
        "Return only an object with exactly proposal_json, a string containing ONE complete JSON interpretation proposal. "
        "That inner proposal must satisfy interpretation_contract supplied beside the source evidence. "
        "The outer string is transport only: do not replace the proposal with prose, summaries, markdown or partial JSON. "
        "Core independently checks the entire inner contract, literal grounding and source ownership. ",
        1,
    )
    data = json.loads(messages[-1]["content"])
    # Source-bearing enum values remain in user evidence, not promoted into
    # higher-authority instructions or silently removed from model context.
    data["interpretation_contract"] = contract
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
        "wire_name": TEXT_NAME,
        "wire_schema": schema,
    }


def decode_proposal(record):
    """Decode the frozen transport name, never today's environment setting."""
    try:
        name = record["options"]["text"]["format"]["name"]
    except (KeyError, TypeError, AttributeError):
        name = None
    if not isinstance(name, str) or name not in (TYPED_NAME, TEXT_NAME):
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
    try:
        value = json.loads(record["result"].get("output_text") or "")
        if name == TYPED_NAME:
            return value
        if name == TEXT_NAME:
            if (
                not isinstance(value, dict)
                or set(value) != {"proposal_json"}
                or not isinstance(value["proposal_json"], str)
            ):
                raise ValueError("Invalid text envelope")
            return json.loads(value["proposal_json"])
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
