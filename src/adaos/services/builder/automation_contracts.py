"""Retain interface discovery across Automation continuations, without grants."""
import json


def retained_external_tool_ids(run_root, source_artifacts):
    previous = (source_artifacts.get("continuation_checkpoint") or {}).get("required_external_tool_ids") or ()
    identities = {value for value in previous if isinstance(value, str) and value.strip()}
    path = run_root / "input" / "external-mcp-contracts.json"
    if path.is_file():
        bundle = json.loads(path.read_text(encoding="utf-8"))
        identities.update(row["id"] for row in bundle.get("contracts") or ()
                          if isinstance(row, dict) and isinstance(row.get("id"), str))
    # These names select refreshed descriptions from the loaded registry.
    # They are neither copied operation schemas nor execution capabilities.
    return sorted(identities)
