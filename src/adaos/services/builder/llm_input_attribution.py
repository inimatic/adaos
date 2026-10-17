"""Content-addressed attribution for Builder model inputs."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from adaos.services.builder_context import ContextBudgetExceeded, ContextSection, compile_sections


SCHEMA = "adaos.builder.llm_input_attribution.v1"
_SAFE_GENERATION_OPTIONS = frozenset(
    {
        "max_tokens",
        "output_mode",
        "patch_stream",
        "prompt_cache_key",
        "prompt_cache_retention",
        "reasoning",
        "request_id",
        "response_format",
        "service_tier",
        "stream",
        "stream_protocol",
        "temperature",
        "text",
        "timeout",
    }
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="replace")


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _estimated_tokens(byte_count: int) -> int:
    # This is deliberately a provider-independent estimate. Provider usage is
    # recorded separately after inference and remains authoritative for cost.
    return math.ceil(max(0, byte_count) / 4)


def _segment(
    messages: Sequence[Mapping[str, Any]], indexes: Sequence[int]
) -> dict[str, Any]:
    resolved_indexes = [index for index in indexes if 0 <= index < len(messages)]
    selected = [dict(messages[index]) for index in resolved_indexes]
    raw = _canonical_bytes(selected)
    return {
        "message_indexes": resolved_indexes,
        "bytes": len(raw),
        "estimated_tokens": _estimated_tokens(len(raw)),
        "sha256": _digest(raw),
    }


def _content_refs(
    items: Sequence[Mapping[str, Any]], *, kinds: set[str]
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in items:
        kind = str(item.get("kind") or "").strip()
        item_id = str(item.get("id") or "").strip()
        if kind not in kinds or not item_id:
            continue
        result.append({"id": item_id, "kind": kind, "sha256": _digest(_canonical_bytes(item))})
    return result


def _text_budget_projection(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Estimate text, not base64 transport bytes or remote image URL length.

    Non-text tokenization depends on provider/model/modality. Keep exact input
    identities in the receipt, but do not falsely reject an image as huge text.
    This projection is for estimation only; submitted messages are unchanged.
    """
    projected = copy.deepcopy([dict(message) for message in messages])
    unestimated_parts = 0
    for message in projected:
        content = message.get("content")
        if content is None or isinstance(content, str):
            continue
        parts = content if isinstance(content, list) else [content]
        estimated_parts = []
        for part in parts:
            if isinstance(part, str) or (
                isinstance(part, Mapping) and part.get("type") in {"text", "input_text", "output_text", "refusal"}
            ):
                estimated_parts.append(part)
            else:
                unestimated_parts += 1
                estimated_parts.append({"type": str(part.get("type") or "unknown")
                                        if isinstance(part, Mapping) else "unknown",
                                        "token_estimate": "provider_modality_estimator_required"})
        message["content"] = estimated_parts
    return projected, unestimated_parts


def build_llm_input_attribution(
    *,
    request_id: str,
    route: str,
    stage: str,
    attempt: int,
    messages: Sequence[Mapping[str, Any]],
    message_purposes: Sequence[str],
    capability_selection: Mapping[str, Any] | None = None,
    model: str | None = None,
    generation_options: Mapping[str, Any] | None = None,
    stable_message_indexes: Sequence[int] | None = None,
    dynamic_message_indexes: Sequence[int] | None = None,
    created_at: str | None = None,
    context_capacity_tokens: int = 64_000,
    context_reserve_tokens: int = 8_192,
    context_target_tokens: int = 8_000,
) -> dict[str, Any]:
    """Build a compact receipt for the exact messages submitted to a provider."""

    normalized_messages = [copy.deepcopy(dict(message)) for message in messages]
    if not normalized_messages:
        raise ValueError("Builder LLM attribution requires at least one message")
    if len(message_purposes) != len(normalized_messages):
        raise ValueError("message_purposes must align with messages")
    if attempt < 1:
        raise ValueError("attempt must be positive")
    stable_indexes = list(stable_message_indexes or ())
    dynamic_indexes = list(dynamic_message_indexes or ())
    if stable_message_indexes is None:
        stable_indexes = [
            index
            for index, purpose in enumerate(message_purposes)
            if purpose
            in {"system_policy", "stable_context", "capability_context"}
        ]
    if dynamic_message_indexes is None:
        dynamic_indexes = [
            index for index in range(len(normalized_messages)) if index not in stable_indexes
        ]

    message_receipts: list[dict[str, Any]] = []
    total_bytes = 0
    for index, (message, purpose) in enumerate(
        zip(normalized_messages, message_purposes, strict=True)
    ):
        content = str(message.get("content") or "")
        raw = content.encode("utf-8", errors="replace")
        total_bytes += len(raw)
        message_receipts.append(
            {
                "index": index,
                "role": str(message.get("role") or "unknown"),
                "purpose": str(purpose or "unspecified"),
                "bytes": len(raw),
                "estimated_tokens": _estimated_tokens(len(raw)),
                "sha256": _digest(raw),
            }
        )

    selection = copy.deepcopy(dict(capability_selection or {}))
    selection_attribution = (
        selection.get("input_attribution")
        if isinstance(selection.get("input_attribution"), Mapping)
        else {}
    )
    profile = str(selection_attribution.get("profile") or "generic").strip()
    if profile not in {"generic", "domain_pack"}:
        profile = "domain_pack" if selection_attribution.get("domain_packs") else "generic"
    items = [
        dict(item)
        for item in selection.get("items") or []
        if isinstance(item, Mapping)
    ]
    examples: list[dict[str, str]] = []
    for item in items:
        for example_index, example in enumerate(item.get("examples") or []):
            examples.append(
                {
                    "id": f"{item.get('id')}.example.{example_index}",
                    "kind": "example",
                    "sha256": _digest(_canonical_bytes(example)),
                }
            )

    safe_options = {
        str(key): copy.deepcopy(value)
        for key, value in (generation_options or {}).items()
        if str(key) in _SAFE_GENERATION_OPTIONS and value is not None
    }
    options_raw = _canonical_bytes(safe_options)
    # JSON-schema output and tool schemas are model input too. Record only
    # content identities here: tool configuration can contain private routing.
    configuration = {key: copy.deepcopy(value) for key, value in (generation_options or {}).items()
                     if key in {"response_format", "text", "tools", "tool_choice"} and value is not None}
    configuration_raw = _canonical_bytes(configuration) if configuration else b""
    messages_raw = _canonical_bytes(normalized_messages)
    request_bytes = len(messages_raw) + len(configuration_raw)
    projected_messages, unestimated_parts = _text_budget_projection(normalized_messages)
    budget_raw = _canonical_bytes(projected_messages)
    budget_boundary = {
        "representation": "canonical_messages_and_configuration_not_provider_transport",
        "full_input_sha256": _digest(messages_raw),
        "full_input_covered": not unestimated_parts,
        "unestimated_content_parts": unestimated_parts,
        "coverage": "text_and_configuration_only" if unestimated_parts else "text_messages_and_configuration",
    }
    try:
        _, final_input_budget = compile_sections(
            [ContextSection("model_input_envelope", budget_raw.decode("utf-8"))],
            capacity_tokens=context_capacity_tokens, reserve_tokens=context_reserve_tokens,
            target_tokens=context_target_tokens, strategy="full",
            overhead_text=configuration_raw.decode("utf-8"),
        )
    except ContextBudgetExceeded as exc:
        exc.receipt.update(budget_boundary)
        raise
    final_input_budget.update(budget_boundary)
    if unestimated_parts:
        final_input_budget["text_budget_status"] = final_input_budget["status"]
        final_input_budget["status"] = "requires_modality_estimate"
    created = created_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema": SCHEMA,
        "request_id": str(request_id),
        "route": str(route),
        "stage": str(stage),
        "attempt": int(attempt),
        "profile": profile,
        "model": str(model or "").strip() or None,
        "final_input_budget": final_input_budget,
        "messages": message_receipts,
        "totals": {
            "messages": len(normalized_messages),
            "bytes": total_bytes,
            "estimated_tokens": _estimated_tokens(total_bytes),
            "estimation": "utf8_bytes_div_4_ceil",
            "request_bytes": request_bytes,
            "request_estimated_tokens": _estimated_tokens(len(budget_raw) + len(configuration_raw)),
            "request_estimation": "utf8_bytes_div_4_ceil_text_and_configuration_only",
            "request_unestimated_content_parts": unestimated_parts,
        },
        "stable_prefix": _segment(normalized_messages, stable_indexes),
        "dynamic_suffix": _segment(normalized_messages, dynamic_indexes),
        "capabilities": {
            "selection_sha256": _digest(_canonical_bytes(selection)),
            "catalog_digest": str(selection.get("catalog_digest") or "").strip()
            or None,
            "component_contracts": _content_refs(items, kinds={"component"}),
            "generic_patterns": _content_refs(items, kinds={"layout", "recipe"}),
            "examples": examples,
            "domain_packs": [
                {
                    key: str(pack.get(key) or "")
                    for key in ("pack_id", "version", "classification", "digest")
                }
                for pack in selection_attribution.get("domain_packs") or []
                if isinstance(pack, Mapping)
            ],
        },
        "generation": {
            "options_sha256": _digest(options_raw),
            "options": safe_options,
            "context_configuration": {
                "bytes": len(configuration_raw),
                "estimated_tokens": _estimated_tokens(len(configuration_raw)),
                "sha256": _digest(configuration_raw),
                "keys": sorted(configuration),
            },
        },
        "created_at": created,
    }


def retain_llm_evidence(value: Mapping[str, Any], *, state_dir=None) -> dict[str, Any]:
    """Acknowledge exact private evidence through the existing verified CAS.

    A caller must retain this reference in its attempt journal before submitting
    another paid effect. These objects have no automatic collection policy.
    They are not exported into platform publications or model context.
    """
    from adaos.services.context_control import ContextControlService
    service = ContextControlService(state_dir=state_dir)
    # Capture the JSON wire representation, including tuple -> array conversion.
    # Reject non-JSON numbers instead of making an unverifiable acknowledgement.
    wire_value = json.loads(json.dumps(dict(value), ensure_ascii=False, allow_nan=False))
    receipt = service.put_artifact(wire_value)
    if service.get_artifact(receipt["ref"]) != wire_value:
        raise ValueError("Builder evidence acknowledgement does not match submitted evidence")
    return {**receipt, "retention_owner": "builder.attempt", "automatic_gc": False,
            "visibility": "private_runtime"}


def read_llm_evidence(receipt: Mapping[str, Any], *, state_dir=None) -> dict[str, Any]:
    """Read and verify a private acknowledgement before reusing its lineage."""
    from adaos.services.context_control import ContextControlService
    ref = str(receipt.get("ref") or "")
    if (not ref.startswith("artifact://context/sha256/")
            or receipt.get("digest") != "sha256:" + ref.rsplit("/", 1)[-1]):
        raise ValueError("Builder evidence receipt identity mismatch")
    value = ContextControlService(state_dir=state_dir).get_artifact(ref)
    if not isinstance(value, dict):
        raise ValueError("Builder attempt evidence must be an object")
    return value


__all__ = ["SCHEMA", "build_llm_input_attribution", "retain_llm_evidence", "read_llm_evidence"]
