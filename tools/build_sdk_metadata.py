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

from adaos.sdk.core.exporter import compatibility_report, export  # noqa: E402


def build_bundle() -> dict[str, object]:
    """Build reproducible metadata from the SDK's canonical exporter."""

    authoring = export(level="rich", include_deprecated=False)
    migration = export(level="rich", include_deprecated=True)
    for projection in (authoring, migration):
        meta = projection.get("meta")
        if isinstance(meta, dict):
            # Wall-clock provenance belongs to a delivery receipt, not to the
            # content-addressed SDK artifact.  Keeping it here made identical
            # Linux and Windows builds produce different digests.
            meta.pop("generated_at", None)
    payload: dict[str, object] = {
        "schema": "adaos.sdk.metadata.bundle.v1",
        "source_revision": str((authoring.get("meta") or {}).get("git_sha") or "unknown"),
        "authoring": authoring,
        "migration": migration,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["digest"] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "build" / "sdk-metadata" / "adaos-sdk-metadata.json",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="Optional previous SDK metadata export or bundle to compare.",
    )
    parser.add_argument(
        "--compatibility-output",
        type=Path,
        default=REPOSITORY_ROOT / "build" / "sdk-metadata" / "compatibility.json",
    )
    parser.add_argument("--fail-on-breaking", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    build_root = (REPOSITORY_ROOT / "build").resolve()
    if not output.is_relative_to(build_root):
        parser.error("--output must stay under the repository build directory")
    payload = build_bundle()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    summary: dict[str, object] = {"output": str(output), "digest": payload["digest"]}
    if args.baseline is not None:
        baseline_path = args.baseline.resolve()
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        if isinstance(baseline, dict):
            if isinstance(baseline.get("migration"), dict):
                baseline = baseline["migration"]
            elif isinstance(baseline.get("authoring"), dict):
                baseline = baseline["authoring"]
        # Migration metadata retains deprecated APIs, so a supported migration
        # path is not misclassified as an immediate removal from authoring.
        report = compatibility_report(dict(baseline), dict(payload["migration"]))
        compatibility_output = args.compatibility_output.resolve()
        if not compatibility_output.is_relative_to(build_root):
            parser.error("--compatibility-output must stay under the repository build directory")
        compatibility_output.parent.mkdir(parents=True, exist_ok=True)
        compatibility_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        summary["compatibility"] = {
            "output": str(compatibility_output),
            "compatible": report["compatible"],
            "breaking_count": len(report["breaking"]),
        }
        print(json.dumps(summary))
        if args.fail_on_breaking and not bool(report["compatible"]):
            return 2
        return 0
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
