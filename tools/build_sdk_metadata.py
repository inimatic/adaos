"""Build the content-addressed AdaOS SDK authoring and migration metadata artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from adaos.sdk.core.exporter import export  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "build" / "sdk-metadata" / "adaos-sdk-metadata.json",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    build_root = (REPOSITORY_ROOT / "build").resolve()
    if not output.is_relative_to(build_root):
        parser.error("--output must stay under the repository build directory")
    payload = {
        "schema": "adaos.sdk.metadata.bundle.v1",
        "authoring": export(level="rich", include_deprecated=False),
        "migration": export(level="rich", include_deprecated=True),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["digest"] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "digest": payload["digest"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
