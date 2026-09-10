"""Content-addressed attribution for Builder model inputs."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


SCHEMA = "adaos.builder.llm_input_attribution.v1"
_SAFE_GENERATION_OPTIONS = frozenset(
    {
        "max_tokens",
        "output_mode",
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
    created = created_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema": SCHEMA,
        "request_id": str(request_id),
        "route": str(route),
        "stage": str(stage),
        "attempt": int(attempt),
        "profile": profile,
        "model": str(model or "").strip() or None,
        "messages": message_receipts,
        "totals": {
            "messages": len(normalized_messages),
            "bytes": total_bytes,
            "estimated_tokens": _estimated_tokens(total_bytes),
            "estimation": "utf8_bytes_div_4_ceil",
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
        },
        "created_at": created,
    }


__all__ = ["SCHEMA", "build_llm_input_attribution"]
