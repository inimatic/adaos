#!/usr/bin/env python3
"""Export one compiled public site as provider-neutral immutable objects."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from adaos.services.public_site_delivery import (  # noqa: E402
    PublicSiteDeliveryError,
    build_delivery_bundle,
    export_delivery_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        bundle = build_delivery_bundle(args.site_dir)
        receipt = export_delivery_bundle(bundle, args.output)
    except (OSError, PublicSiteDeliveryError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **receipt}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
