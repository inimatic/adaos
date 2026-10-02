from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from .model import RootMcpToolContract


DEFAULT_REVIEW_CHARS = 16_000
DEFAULT_OPTIMIZE_CHARS = 64_000


def measure_output(
    value: Any,
    *,
    review_chars: int = DEFAULT_REVIEW_CHARS,
    optimize_chars: int = DEFAULT_OPTIMIZE_CHARS,
) -> dict[str, Any]:
    """Measure the canonical compact JSON representation of one MCP result."""

    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    encoded = text.encode("utf-8")
    chars = len(text)
    review_limit = max(1, int(review_chars))
    optimize_limit = max(review_limit, int(optimize_chars))
    if chars > optimize_limit:
        signal = "optimize"
    elif chars > review_limit:
        signal = "review"
    else:
        signal = "ok"
    return {
        "serialized_chars": chars,
        "serialized_bytes": len(encoded),
        "estimated_tokens": max(1, math.ceil(len(encoded) / 4)),
        "output_digest": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "optimization_signal": signal,
    }


def profile_audit_events(
    events: Iterable[Mapping[str, Any]],
    *,
    top_n: int = 10,
) -> dict[str, Any]:
    """Aggregate measured Root MCP audit rows without retaining response bodies."""

    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0,
            "measured_calls": 0,
            "total_chars": 0,
            "max_chars": 0,
            "max_bytes": 0,
            "signals": {"ok": 0, "review": 0, "optimize": 0},
        }
    )
    event_count = 0
    measured_event_count = 0
    for event in events:
        if not isinstance(event, Mapping):
            continue
        event_count += 1
        tool_id = str(event.get("tool_id") or "unknown").strip() or "unknown"
        row = groups[tool_id]
        row["calls"] += 1
        summary = event.get("result_summary")
        if not isinstance(summary, Mapping):
            continue
        chars = summary.get("serialized_chars")
        output_bytes = summary.get("serialized_bytes")
        if not isinstance(chars, int) or chars < 0:
            continue
        measured_event_count += 1
        row["measured_calls"] += 1
        row["total_chars"] += chars
        row["max_chars"] = max(int(row["max_chars"]), chars)
        if isinstance(output_bytes, int):
            row["max_bytes"] = max(int(row["max_bytes"]), output_bytes)
        signal = str(summary.get("optimization_signal") or "ok").strip().lower()
        if signal not in row["signals"]:
            signal = "ok"
        row["signals"][signal] += 1

    tools: list[dict[str, Any]] = []
    for tool_id, raw in groups.items():
        measured = int(raw["measured_calls"])
        tools.append(
            {
                "tool_id": tool_id,
                "calls": int(raw["calls"]),
                "measured_calls": measured,
                "max_chars": int(raw["max_chars"]),
                "max_bytes": int(raw["max_bytes"]),
                "average_chars": (
                    round(int(raw["total_chars"]) / measured, 1) if measured else None
                ),
                "signals": dict(raw["signals"]),
            }
        )
    tools.sort(key=lambda item: (-int(item["max_chars"]), item["tool_id"]))
    bounded_top = max(1, min(int(top_n), 100))
    return {
        "schema": "adaos.mcp.output_profile.v1",
        "event_count": event_count,
        "measured_event_count": measured_event_count,
        "tool_count": len(tools),
        "top": tools[:bounded_top],
    }


def audit_search_contracts(
    contracts: Sequence[RootMcpToolContract],
) -> dict[str, Any]:
    """Find search-like MCP contracts that cannot bound and continue results."""

    rows: list[dict[str, Any]] = []
    for contract in contracts:
        operation = contract.id.rsplit(".", 1)[-1].lower()
        if not operation.startswith(("search", "query")):
            continue
        schema = contract.input_schema if isinstance(contract.input_schema, Mapping) else {}
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        bound_name = next(
            (name for name in ("limit", "top_k", "page_size") if name in properties),
            None,
        )
        cursor_name = next(
            (
                name
                for name in ("cursor", "page_token", "pagination_token", "offset")
                if name in properties
            ),
            None,
        )
        signals: list[str] = []
        if bound_name is None:
            signals.append("missing_top_k")
        else:
            bound_schema = properties.get(bound_name)
            if not isinstance(bound_schema, Mapping) or not isinstance(
                bound_schema.get("maximum"), int
            ):
                signals.append("unbounded_top_k")
        if cursor_name is None:
            signals.append("missing_pagination")
        rows.append(
            {
                "tool_id": contract.id,
                "bound_argument": bound_name,
                "pagination_argument": cursor_name,
                "signals": signals,
                "status": "review" if signals else "ok",
            }
        )
    rows.sort(key=lambda item: (item["status"] == "ok", item["tool_id"]))
    return {
        "schema": "adaos.mcp.search_contract_audit.v1",
        "search_tool_count": len(rows),
        "review_count": sum(1 for item in rows if item["signals"]),
        "items": rows,
    }


__all__ = [
    "DEFAULT_OPTIMIZE_CHARS",
    "DEFAULT_REVIEW_CHARS",
    "audit_search_contracts",
    "measure_output",
    "profile_audit_events",
]
