"""Provider-native MCP evidence, kept separate from host preflight calls."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from adaos.services.builder_context import digest


def mcp_trace(envelope: dict[str, Any]) -> dict[str, Any]:
    response = envelope.get("response") or {}
    items = [item for item in response.get("output", []) if isinstance(item, dict) and str(item.get("type", "")).startswith("mcp_")]
    calls = []
    spans = {span["item_id"]: span for span in (envelope.get("progress") or {}).get("tool_spans", [])
             if span.get("kind") == "mcp_call"}
    for item in items:
        if item.get("type") != "mcp_call":
            continue
        args, output = item.get("arguments"), item.get("output")
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except ValueError:
            args = {"unparsed": True}
        try:
            decoded = json.loads(output) if isinstance(output, str) else output
        except ValueError:
            decoded = None
        tool_error = (isinstance(decoded, dict) and (decoded.get("ok") is False or decoded.get("isError") is True
            or bool(decoded.get("error"))))
        provider_error = item.get('error') if isinstance(item.get('error'), dict) else {}
        error_type = provider_error.get('type')
        tool_error = bool(tool_error) or error_type == 'mcp_tool_execution_error'
        # Root tool replies use an {ok,result} envelope inside MCP text content.
        if isinstance(decoded, dict) and isinstance(decoded.get("result"), dict):
            decoded = decoded["result"]
        observed = spans.get(item.get("id"), {})
        calls.append({"id": item.get("id"), "name": item.get("name"), "arguments": args,
            "observed_duration_ms": observed.get("duration_ms"), "observed_started_at": observed.get("started_at"),
            "observed_finished_at": observed.get("finished_at"),
            "error": item.get("error"), "tool_result_error": bool(tool_error), "status": item.get("status"), "output_sha256": digest(output or ""),
            'provider_error_type': error_type, 'protocol_error': error_type == 'mcp_protocol_error',
            "output_bytes": len(json.dumps(output, ensure_ascii=False).encode("utf-8")),
            "fragment_id": decoded.get("fragment", {}).get("id") if isinstance(decoded, dict) else None,
            "fragment_ids": [part.get("id") for part in decoded.get("fragments", [])] if isinstance(decoded, dict) else [],
            "library_digest": decoded.get("library_digest") if isinstance(decoded, dict) else None})
    frequencies = Counter(digest({"name": call["name"], "arguments": call["arguments"]}) for call in calls)
    return {"schema": "adaos.e2e.prototype_native_mcp_trace.v1", "provider_job_id": envelope.get("job_id"),
        "model": response.get("model") or envelope.get("model"), "call_count": len(calls),
        "discovery_count": sum(item.get("type") == "mcp_list_tools" for item in items),
        "errors": sum(bool(call["error"]) or call["tool_result_error"] or call["status"] == "failed" for call in calls),
        'protocol_errors': sum(call['protocol_error'] for call in calls),
        'tool_execution_errors': sum(call['tool_result_error'] for call in calls),
        "repeated_exact_calls": sum(count - 1 for count in frequencies.values()), "calls": calls,
        "usage": response.get("usage"), "item_types": dict(Counter(item.get("type") for item in items)),
        "mcp_observed_interval_ms": (envelope.get("telemetry") or {}).get("timing", {}).get("mcp_observed_interval_ms"),
        "call_latency": "receive-side provider lifecycle intervals, not pure MCP server compute" if spans
            else "not exposed by provider terminal output; job wall time measured separately"}
