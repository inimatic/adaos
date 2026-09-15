"""Recover one owned TEST's exact failed prototype through Builder's tool API."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creation", type=Path, required=True)
    parser.add_argument("--intent", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    created = json.loads(args.creation.read_text(encoding="utf-8"))["created_test"]
    intent = json.loads(args.intent.read_text(encoding="utf-8"))
    output = args.output.resolve()
    if (os.getenv("ENV_TYPE") != "dev" or not created["id"].startswith("workbench_test_")
            or created["result"]["project"]["created_by"] != "builder.user"
            or intent["id"] != created["id"] or intent["automation_authorized"] is not False
            or not output.is_relative_to((Path.cwd() / "e2e/artifacts/builder").resolve()) or output.exists()):
        parser.error("Exact owned TEST provenance and unused E2E output required")
    output.mkdir(parents=True)
    hub = "http://127.0.0.1:8778"
    client = requests.Session()
    client.trust_env = False
    client.headers["X-AdaOS-Token"] = resolve_control_token(base_url=hub)

    def call(name, arguments, timeout=60):
        response = client.post(hub + "/api/tools/call", json={"tool": "builder_skill:" + name,
            "arguments": {**arguments, "webspace_id": "desktop-dev"}}, timeout=timeout)
        body = response.json()
        (output / (name + ".json")).write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        response.raise_for_status()
        if body.get("ok") is not True:
            raise RuntimeError(f"Builder {name} failed; inspect its retained response before any retry")
        return body.get("result", body)

    current = call("get_session", {"session_id": "scenario." + created["id"]})
    session = current.get("session") or {}
    if session.get("scenario_id") != created["id"] or str(session.get("ui_revision")) != intent["base_revision"]:
        raise ValueError("Current Builder target/revision differs from the exact failed intent")
    arguments = {"scenario_id": created["id"], "instruction": intent["prompt"],
        "retry_job_id": args.job, "expected_ui_revision": intent["base_revision"], "auto_apply": True}
    (output / "retry-intent.json").write_text(json.dumps(arguments, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"submitted_once": True, "job": args.job, "revision": intent["base_revision"]}), flush=True)
    result = call("update_current_scenario", arguments, timeout=900)
    print(json.dumps({key: result.get(key) for key in ("ok", "status", "error", "ui_revision")}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
