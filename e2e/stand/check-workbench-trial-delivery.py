"""Reconcile the retained TEST beta using Builder's public publication command."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from adaos.apps.cli.active_control import resolve_control_token
from adaos.e2e.builder import _write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    root = Path.cwd().resolve()
    output = args.output.resolve()
    admission = json.loads(args.admission.read_text(encoding="utf-8"))
    identifier = admission["scenario"]
    if (os.getenv("ENV_TYPE") != "dev" or output.exists()
            or not output.is_relative_to(root / "e2e/artifacts/builder")
            or not identifier.startswith("workbench_test_") or admission["reviewer"]["kind"] != "agent"):
        parser.error("Owned, independently reviewed TEST and new DEV evidence required")
    for evidence in admission["reports"]:
        assert hashlib.sha256(Path(evidence["path"]).read_bytes()).hexdigest() == evidence["sha256"]
    hub = "http://127.0.0.1:8778"
    report = {"scope": "Builder command local beta delivery; no stable or external publication", "passed": False}
    try:
        with requests.Session() as client:
            client.trust_env = False
            client.headers["X-AdaOS-Token"] = resolve_control_token(base_url=hub)
            command = {"tool": "builder_sdk_control_skill:publish_project", "dev": True,
                       "arguments": {"object_type": "project", "object_id": identifier,
                                     "dry_run": True, "confirmed": True, "webspace_id": "desktop-dev"},
                       "context": {"webspace_id": "desktop-dev", "action_approval": {
                           "status": "approve", "approved_by": "agent:delegated-test-review",
                           "risk_class": "network"}}}
            report["command"] = command
            response = client.post(hub + "/api/tools/call", json=command, timeout=180)
            report["response"] = response.json()
            response.raise_for_status()
            result = report["response"]["result"]
            assert report["response"]["ok"] and result["trial_ready"]
            assert result["workflow"]["automation"]["head_task_id"] == admission["task"]
            report["candidate_id"] = result["candidate"]["candidate_id"]
            response = client.get(hub + "/api/node/yjs/webspaces/desktop/catalog/apps", timeout=30)
            response.raise_for_status()
            report["launchers"] = [item for item in response.json()["items"]
                                   if item.get("scenario_id") == identifier]
            assert len(report["launchers"]) == 1
            assert report["launchers"][0]["release_stage"] == "beta"
            assert report["launchers"][0]["component_update"]["candidate"]["id"] == report["candidate_id"]
            report["passed"] = True
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        _write_json(output, report)
    print(json.dumps({"passed": report["passed"], "failure": report.get("failure"), "output": str(output)}))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
