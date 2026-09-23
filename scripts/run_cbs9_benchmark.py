from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaos.services.capability_binding_state.benchmark_program import (
    run_bounded_cbs9_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the bounded matched CBS9 benchmark")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--historical-telemetry",
        type=Path,
        action="append",
        default=[],
        help="File or directory containing immutable CBS5 telemetry (repeatable)",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = run_bounded_cbs9_benchmark(
        output / "work",
        historical_telemetry_paths=args.historical_telemetry,
    )
    (output / "benchmark-bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "bundle_digest": bundle["bundle_digest"],
        "report": bundle["report"],
        "artifact": str(output / "benchmark-bundle.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
