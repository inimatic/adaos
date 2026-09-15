"""Privately compare declared settings and credential references across channels."""

import argparse
import json
from pathlib import Path

from adaos.services.applications.configuration import ApplicationConfigurationStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "verify"))
    parser.add_argument("--application", required=True)
    parser.add_argument("--component", required=True)
    parser.add_argument("--channel", choices=("stable", "beta"), required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    baseline = args.baseline.resolve()
    if not baseline.is_relative_to(root / ".adaos/recovery"):
        parser.error("Baseline must remain in private recovery storage")
    store = ApplicationConfigurationStore(root / ".adaos/state", args.application, args.component)
    current = store.read().get(args.channel)
    if not current or current["release_digest"] != args.release:
        raise ValueError("Exact installed configuration channel and release required")
    identity = {"application_id": args.application, "component_ref": args.component}
    values = {"values": current["values"], "credentials": current["credentials"]}
    if args.mode == "capture":
        baseline.parent.mkdir(parents=True, exist_ok=True)
        with baseline.open("x", encoding="utf-8") as handle:
            json.dump({**identity, **values}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    previous = json.loads(baseline.read_text(encoding="utf-8"))
    if any(previous.get(key) != value for key, value in identity.items()):
        raise ValueError("Configuration baseline belongs to another owner")
    checks = []
    for kind in ("values", "credentials"):
        expected, observed = previous[kind], values[kind]
        missing = sum(key not in observed or observed[key] != value for key, value in expected.items())
        checks.append({"kind": kind, "baseline_count": len(expected), "current_count": len(observed), "missing_or_changed": missing})
    passed = all(item["missing_or_changed"] == 0 for item in checks)
    print(json.dumps({"passed": passed, "checks": checks}, ensure_ascii=False))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
