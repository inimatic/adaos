"""Generate or verify Root's immutable public Prototype guidance projection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaos.services.builder.prototype_mcp import library_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] /
        "src/adaos/integrations/adaos-backend/backend/prototype-context-library.json")
    args = parser.parse_args()
    snapshot = library_snapshot()
    if args.check:
        if json.loads(args.output.read_text(encoding="utf-8")) != snapshot:
            raise SystemExit("Root Prototype context projection differs from its Core authority")
    else:
        args.output.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checked": args.check, "library_digest": snapshot["library_digest"],
                      "fragments": len(snapshot["fragments"])}))


if __name__ == "__main__":
    main()
