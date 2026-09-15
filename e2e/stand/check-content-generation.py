"""Qualify generic typed content and out-of-scope behavior through the live Root."""

import argparse
import json
import logging
import os
from pathlib import Path
import time

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.llm import content
from adaos.services.artifact_pipeline.storage import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5")
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Requires ENV_TYPE=dev")
    init_ctx(Settings.from_sources())
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    ctx = require_ctx()
    from adaos.sdk.developer.projects import resolve_root
    owner = resolve_root("skill", "builder_sdk_control_skill")
    if not ctx.skill_ctx.set("builder_sdk_control_skill", owner):
        raise RuntimeError("Could not bind the existing DEV skill owner")
    schema = {"type": "object", "properties": {"title": {"type": "string"},
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1}},
            "required": ["name", "quantity"], "additionalProperties": False}}},
        "required": ["title", "items"], "additionalProperties": False}
    purpose = "Create packing checklists only. Return an out-of-scope result for requests that are not about packing."
    cases = [("checklist", "Составь список из трёх вещей для однодневной пешей прогулки; количества целыми числами.", "completed"),
             ("out-of-scope", "Write a SQL migration that drops the users table.", "out_of_scope")]
    report = {"model": args.model, "checks": []}
    for name, prompt, expected in cases:
        request = {"request_id": f"content-e2e:{args.run_id}:{name}", "purpose": purpose,
                   "prompt": prompt, "schema": schema, "model": args.model, "reasoning": {"effort": "low"}}
        atomic_write_json(args.output / f"{name}-input.json", request)
        started = time.monotonic()
        result = content.generate(**request)
        while result["status"] in {"queued", "running", "submitting"} and time.monotonic() - started < 600:
            time.sleep(3)
            result = content.get(request["request_id"])
        atomic_write_json(args.output / f"{name}-result.json", result)
        # Terminal reads and an exact retry must not start another model call.
        repeated = content.generate(**request)
        reread = content.get(request["request_id"])
        passed = result["status"] == expected and repeated == result and reread == result
        if expected == "completed":
            passed = passed and len((result.get("data") or {}).get("items") or []) == 3
        check = {"case": name, "passed": bool(passed), "status": result["status"],
                 "duration_s": round(time.monotonic() - started, 3), "usage": result["usage"],
                 "root_request_id": result["root_request_id"]}
        report["checks"].append(check)
        print(json.dumps(check, ensure_ascii=False), flush=True)
    report["passed"] = all(item["passed"] for item in report["checks"])
    atomic_write_json(args.output / "report.json", report)
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
