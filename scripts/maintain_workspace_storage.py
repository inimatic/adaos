"""Stable unattended entry point; writes one latest receipt in maintenance state."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaos.services.workspace_storage import GIB, inspect_workspace_storage, maintain_workspace_storage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, default=ROOT)
    parser.add_argument("--apply", action="store_true", help="Clean only expired managed scratch and qualified worktrees.")
    parser.add_argument("--check", action="store_true", help="Check allocation admission without planning cleanup.")
    parser.add_argument("--reserve-gib", type=float, default=0)
    args = parser.parse_args()
    if args.reserve_gib < 0 or (args.check and args.apply):
        parser.error("reserve must be nonnegative; --check and --apply are mutually exclusive")
    if args.check:
        payload = inspect_workspace_storage(args.workspace_root, reserve_bytes=int(args.reserve_gib * GIB))
        ok = payload["ok"]
    else:
        payload = maintain_workspace_storage(args.workspace_root, apply=args.apply)
        ok = payload["ok"] and payload["report"]["ok"]
    if sys.stdout is not None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
