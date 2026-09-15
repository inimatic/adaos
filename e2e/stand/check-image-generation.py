"""Qualify an owned TEST icon draft through verified local ingress and deployed Root."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.e2e.builder import _write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creation", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    output = args.output.resolve()
    created = json.loads(args.creation.read_text(encoding="utf-8"))["created_test"]
    if (os.getenv("ENV_TYPE") != "dev" or not created["id"].startswith("workbench_test_")
            or created["result"]["project"]["created_by"] != "builder.user" or output.exists()
            or not output.is_relative_to((Path.cwd() / "e2e/artifacts/builder").resolve())):
        parser.error("Owned native TEST receipt and new confined output required")
    output.mkdir(parents=True)
    report = {"scope": "Verified Builder image draft, not source apply, crop UI or publication",
              "project_id": created["id"], "model": args.model, "passed": False}
    try:
        if args.prepare:
            from adaos.apps.cli.app import Settings, init_ctx
            from adaos.apps.cli.commands.skill import _notify_hub_skill_activated
            from adaos.services.agent_context import get_ctx
            from adaos.services.developer_project_validation import _manager

            init_ctx(Settings.from_sources())
            manager = _manager(get_ctx())
            name = "builder_sdk_control_skill"
            prepared = manager.prepare_dev_runtime(name, run_tests=True)
            report["runtime"] = {"version": prepared.version, "slot": prepared.slot,
                                 "tests": {key: value.status for key, value in (prepared.tests or {}).items()}}
            manager.activate_for_space(name, space="dev", version=prepared.version, slot=prepared.slot,
                                       webspace_id="desktop-dev", defer_webspace_rebuild=True)
            if not _notify_hub_skill_activated(name, space="dev", webspace_id="desktop-dev", defer_webspace_rebuild=True):
                raise RuntimeError("DEV runtime activation notification failed")
        hub = "http://127.0.0.1:8778"
        client = requests.Session()
        client.trust_env = False
        client.headers["X-AdaOS-Token"] = resolve_control_token(base_url=hub)

        def call(tool, arguments, *, allow_approval=False):
            response = client.post(hub + "/api/tools/call", json={"tool": "builder_sdk_control_skill:" + tool,
                "arguments": {"object_type": "project", "object_id": created["id"],
                              "webspace_id": "desktop-dev", **arguments}}, timeout=90)
            body = response.json()
            detail = body.get("detail") or {}
            if allow_approval and detail.get("error") == "action_approval_required":
                if detail.get("tool") != "builder_sdk_control_skill:generate_icon" or not detail.get("pending_action_id"):
                    raise RuntimeError("Unexpected image approval target")
                _write_json(output / "approval-required.json", body)
                subprocess.run(["node", str(Path(__file__).parent / "browser/approve-owned-image.mjs")], check=True,
                    env={**os.environ, "ADAOS_E2E_HUB_URL": hub, "ADAOS_E2E_HUB_TOKEN": client.headers["X-AdaOS-Token"],
                         "ADAOS_E2E_ACTION_ID": detail["pending_action_id"], "ADAOS_E2E_OUTPUT": str(output)})
                return call(tool, arguments)
            if not response.ok or body.get("ok") is not True:
                _write_json(output / "failure-response.json", body)
                raise RuntimeError(f"{tool} rejected ({response.status_code}); inspect retained response before retry")
            return body["result"]

        request = {"request_id": args.run_id, "model": args.model,
                   "prompt": "An open book with a small green bookmark, crisp simple shapes on white, suitable for a reading-list application icon."}
        _write_json(output / "intent.json", request)
        started = time.monotonic()
        result = call("generate_icon", request, allow_approval=True)
        _write_json(output / "draft.json", result)
        while result["status"] in {"queued", "running", "submitting", "in_progress"} and time.monotonic() - started < 600:
            time.sleep(3)
            result = call("get_icon_generation", {"request_id": result["request_id"]})
            _write_json(output / "draft.json", result)
        report.update(status=result["status"], duration_s=round(time.monotonic() - started, 3),
                      root_request_id=result.get("root_request_id"), usage=result.get("usage"),
                      metering_status=result.get("metering_status"))
        if result["status"] != "completed":
            raise RuntimeError("Image draft is not completed; retain the request and inspect before another submission")
        repeated = call("generate_icon", request)
        reread = call("get_icon_generation", {"request_id": result["request_id"]})
        assert result == repeated == reread, "An exact retry must reuse the same image and Root request"
        media = result["media"]
        assert media["width"] == media["height"] == 1024 and media["sha256"]
        report.update(passed=True, media=media, exact_retry_reused=True)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        _write_json(output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
