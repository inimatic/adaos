#!/usr/bin/env python3
"""Export the active retained Site Studio revision as a PS3 delivery directory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaos.services.site_studio.publication_export import export_publication  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--files-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = export_publication(args.database, args.files_root, args.output)
    print(json.dumps({"ok": True, "output": str(args.output.resolve()), **receipt}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
