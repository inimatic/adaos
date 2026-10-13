"""Generate/check the bounded C2 Client registration and public input types."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from adaos.services.ui_component_contracts import component_registration_types  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--client", type=Path, default=ROOT / "src/adaos/integrations/adaos-client")
    args = parser.parse_args()
    output = args.client / "src/app/runtime/component-contract.generated.ts"
    rendered = component_registration_types()
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            print("Component contract projection is missing/stale: " + str(output))
            return 1
        print("Component contract projection is current.")
    else:
        output.write_text(rendered, encoding="utf-8", newline="\n")
        print("Generated " + str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
