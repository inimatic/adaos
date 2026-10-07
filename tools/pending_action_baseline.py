"""Build redacted, content-addressed Pending Actions baseline evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from adaos.services.pending_action_inventory import build_pending_action_baseline  # noqa: E402


def _object(path: Path, *, name: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def _array(path: Path, *, name: str) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{name} must contain a JSON array of objects")
    return [dict(item) for item in value]


def _artifact(path: Path, role: str) -> dict[str, Any]:
    raw = path.read_bytes()
    return {
        "role": role,
        "digest": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "bytes": len(raw),
        "redaction": "content_and_path_not_embedded",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--revisions", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--sdk-discovery", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--failures", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "e2e" / "artifacts" / "pending-actions" / "baseline.json",
    )
    args = parser.parse_args(argv)

    inputs = {
        "snapshot": args.snapshot.resolve(),
        "revisions": args.revisions.resolve(),
        "topology": args.topology.resolve(),
        "sdk_discovery": args.sdk_discovery.resolve(),
        "sample": args.sample.resolve(),
    }
    if args.failures is not None:
        inputs["failures"] = args.failures.resolve()
    output = args.output.resolve()
    allowed_roots = (
        (REPOSITORY_ROOT / ".tmp").resolve(),
        (REPOSITORY_ROOT / "e2e" / "artifacts").resolve(),
    )
    if not any(output.is_relative_to(root) for root in allowed_roots):
        parser.error("--output must stay under .tmp/ or e2e/artifacts/")

    failures = _array(inputs["failures"], name="failures") if "failures" in inputs else []
    evidence = build_pending_action_baseline(
        _object(inputs["snapshot"], name="snapshot"),
        component_revisions=_object(inputs["revisions"], name="revisions"),
        topology=_object(inputs["topology"], name="topology"),
        sdk_discovery=_object(inputs["sdk_discovery"], name="sdk-discovery"),
        sample=_object(inputs["sample"], name="sample"),
        failures=failures,
        artifacts=[_artifact(path, role) for role, path in sorted(inputs.items())],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"output": str(output), "digest": evidence["digest"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
