"""Fail a schema/manifest review when new persistent CBS terms are ambiguous."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from adaos.services.capability_binding_state import lint_persistent_terminology


def _load(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if path.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    raise ValueError(f"unsupported persistent document type: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flag persistent state_ref and ambiguous capabilities declarations."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    issue_total = 0
    for path in args.paths:
        for issue in lint_persistent_terminology(_load(path), root=str(path)):
            issue_total += 1
            print(f"{issue.path}: {issue.code}: {issue.message}")
    return 1 if issue_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
