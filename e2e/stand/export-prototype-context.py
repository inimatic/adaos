"""Print the deterministic public Core guidance snapshot consumed by Root MCP.

The tracked snapshot is deliberately checked against live Core in tests and at
session admission. Use an editor/apply_patch to update it; do not publish private
project context or inferred memories into this library.
"""
import json
from adaos.services.builder.prototype_mcp import library_snapshot

if __name__ == "__main__":
    # ASCII transport avoids locale-dependent Windows stdout corruption.
    print(json.dumps(library_snapshot(), ensure_ascii=True, sort_keys=True, indent=2))
