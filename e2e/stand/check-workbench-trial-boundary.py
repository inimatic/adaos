"""Read-only live check of the owned TEST Trial's no-fallback boundary."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.e2e.builder import _write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    root = Path.cwd().resolve()
    output = args.output.resolve()
    receipt = json.loads(args.trial.read_text(encoding="utf-8"))
    identifier = receipt["scenario"]
    if (os.getenv("ENV_TYPE") != "dev" or output.exists()
            or not output.is_relative_to(root / "e2e/artifacts/builder")
            or not receipt["passed"] or not identifier.startswith("workbench_test_")):
        parser.error("Requires owned TEST receipt, DEV and new Builder evidence")
    placement = receipt["placement"]
    target = placement["target"]["webspace_id"]
    assert target == "desktop-dev-dev" and placement["data_mode"] == "empty"
    database = root / f".adaos/dev/sn_6acf0c01/skills/.runtime/{identifier}_skill/v0.1/data/reading_list.sqlite3"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    report = {"scope": "Read-only TEST Trial executor denial; not functional acceptance",
              "scenario": identifier, "candidate": receipt["delivery"]["candidate_id"],
              "calls": [], "passed": False, "dev_database_before": before}
    hub = "http://127.0.0.1:8778"
    try:
        for routing in ("arguments", "context"):
            for dev in (False, True):
                response = requests.post(hub + "/api/tools/call", headers={
                    "X-AdaOS-Token": resolve_control_token(base_url=hub)}, json={
                    "tool": identifier + "_skill:list_books", "dev": dev,
                    routing: {"webspace_id": target}}, timeout=30)
                payload = response.json()
                denied = response.status_code == 409 and payload.get("detail", {}).get("error") == "trial_runtime_unavailable"
                report["calls"].append({"routing": routing, "dev": dev, "status": response.status_code,
                                        "denied": denied, "response": payload})
        report["dev_database_after"] = hashlib.sha256(database.read_bytes()).hexdigest()
        report["dev_unchanged"] = report["dev_database_after"] == before
        report["passed"] = report["dev_unchanged"] and all(call["denied"] for call in report["calls"])
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        _write_json(output, report)
    print(json.dumps({"passed": report["passed"], "statuses": [call["status"] for call in report["calls"]]}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
