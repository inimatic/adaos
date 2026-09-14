"""Qualify DEV Subscriptions against an exact completed native TEST task."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.apps.cli.commands.skill import _notify_hub_skill_activated
from adaos.e2e.builder import _write_json
from adaos.services.agent_context import get_ctx
from adaos.services.developer_project_validation import _manager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true", help="Test and activate only DEV subscription_status_skill")
    args = parser.parse_args()
    load_dotenv()
    output = args.output.resolve()
    if os.getenv("ENV_TYPE") != "dev" or output.exists() or not output.is_relative_to(Path.cwd() / "e2e/artifacts/builder"):
        parser.error("Requires DEV and new Builder evidence")
    admitted = json.loads(args.start.read_text(encoding="utf-8"))["result"]["session"]
    identifier = admitted["object_id"]
    if not identifier.startswith("workbench_test_"):
        parser.error("Explicit native TEST task required")
    current = json.loads(Path(f".adaos/state/builder/automation/scenario.{identifier}.json").read_text(encoding="utf-8"))
    if current["current_task_id"] != admitted["current_task_id"] or current["status"] != "completed":
        parser.error("Exact completed task required")
    model = current["agent_profile"]["model"]
    usage = current["codex_usage_accounting"]
    if usage.get("status") != "reported" or usage.get("task_id") != current["current_task_id"] or usage.get("model") != model:
        parser.error("Matching Root usage acknowledgement required")
    report = {"task_id": current["current_task_id"], "model": model, "passed": False,
              "task_usage": usage,
              "scope": "DEV Subscriptions model projection, not billing or browser acceptance"}
    try:
        if args.prepare:
            init_ctx(Settings.from_sources())
            manager = _manager(get_ctx())
            name = "subscription_status_skill"
            prepared = manager.prepare_dev_runtime(name, run_tests=True)
            report["runtime"] = {"version": prepared.version, "slot": prepared.slot,
                                 "tests": {key: value.status for key, value in (prepared.tests or {}).items()}}
            manager.activate_for_space(name, space="dev", version=prepared.version, slot=prepared.slot,
                                       webspace_id="desktop-dev", defer_webspace_rebuild=True)
            if not _notify_hub_skill_activated(name, space="dev", webspace_id="desktop-dev", defer_webspace_rebuild=True):
                raise RuntimeError("DEV runtime activation notification failed")
        hub = "http://127.0.0.1:8778"
        response = requests.post(hub + "/api/tools/call", headers={"X-AdaOS-Token": resolve_control_token(base_url=hub)},
            json={"tool": "subscription_status_skill:refresh_status", "arguments": {"webspace_id": "desktop-dev"}}, timeout=60)
        value = response.json()
        assert response.ok and value.get("ok") is True, value
        result = value["result"]
        report["refresh"] = result.get("refresh")
        report["codex_models"] = result.get("codex_models")
        assert result.get("ok") and result.get("refresh", {}).get("ok"), "Root refresh did not succeed"
        rows = result["codex_models"]["items"]
        row = next(item for item in rows if item["model"] == model)
        assert row.get("fresh_input_tokens", 0) >= usage["input_tokens"] - usage["cached_input_tokens"], row
        assert row.get("cached_input_tokens", 0) >= usage["cached_input_tokens"], row
        assert row.get("output_tokens", 0) >= usage["output_tokens"], row
        assert row.get("cost_status") in {"estimated", "partial", "unpriced"}, row
        if row["cost_status"] == "unpriced":
            assert row["estimated_usd"] == "", "Absent tariff must not be shown as zero cost"
        report["passed"] = True
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        _write_json(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
