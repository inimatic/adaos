"""Review retained Builder checkpoints locally without approving their revisions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

from dotenv import load_dotenv

from adaos.apps.cli.active_control import resolve_control_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--builder-webspace", help="Explicit DEV Builder host for Conversation review")
    parser.add_argument("--probe", choices=("review", "interactions", "commands", "empty", "readonly", "capabilities", "conversation"), default="review")
    parser.add_argument("--field-type", choices=("shortText", "longText", "date", "time", "number", "integer", "dropdown", "singleChoice"))
    args = parser.parse_args()
    if args.probe == "conversation" and not args.builder_webspace:
        parser.error("Conversation review requires --builder-webspace (a DEV Builder host, not its E2E source or application preview)")
    if args.field_type and args.probe != "interactions":
        parser.error("--field-type requires --probe interactions")
    load_dotenv(".env")
    if os.environ.get("ENV_TYPE") != "dev":
        raise SystemExit("Browser cohort reviews require ENV_TYPE=dev")
    hub = "http://127.0.0.1:8778"
    environment = {**os.environ, "ADAOS_E2E_HUB_URL": hub,
                   "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=hub)}
    summary = []
    review_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    revisions = {
        "core": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "client": subprocess.check_output(["git", "-C", "src/adaos/integrations/adaos-client", "rev-parse", "HEAD"], text=True).strip(),
        "client_dirty": bool(subprocess.check_output(["git", "-C", "src/adaos/integrations/adaos-client", "status", "--porcelain"], text=True).strip()),
    }
    scripts = {"review": "prototype-review.mjs", "empty": "prototype-review.mjs", "interactions": "prototype-interactions.mjs",
               "commands": "prototype-command-probe.mjs", "readonly": "prototype-interactions.mjs",
               "capabilities": "prototype-capabilities.mjs", "conversation": "builder-conversation.mjs"}
    for checkpoint in sorted(args.run.glob(f"checkpoints/*/attempt-{args.attempt:02}.json")):
        case = checkpoint.parent.name
        if args.case and case not in args.case:
            continue
        value = json.loads(checkpoint.read_text(encoding="utf-8"))
        ownership = value.get("cleanup", {})
        if not ownership.get("test") or ownership.get("acceptance") != "not_approved":
            continue
        created = next(step["output"] for step in value["steps"] if step["id"] == "create")
        preview = next((item for item in ownership.get("previews", [])
                        if item["scenario_id"] == created["scenario_id"] and item.get("test")), None)
        if not preview:
            continue
        source = Path(created["artifact_root"])
        application = json.loads((source / "webui.json").read_text(encoding="utf-8"))["ui"]["application"]
        widgets = application["desktop"]["pageSchema"]["widgets"]
        details = next((item for item in widgets if item["type"] == "item.details"), None)
        collection = next((item for item in widgets if details and item["type"] in ("ui.list", "ui.table")
                           and item.get("dataSource", {}).get("resourceType") == details.get("dataSource", {}).get("resourceType")), None)
        output = args.run / f"browser-{args.probe}" / f"{case}-{args.attempt:02}"
        if output.exists() and any(output.iterdir()):
            output = output / review_id
        output.mkdir(parents=True, exist_ok=True)
        env = {**environment, "ADAOS_E2E_CHECKPOINT": str(checkpoint.resolve()),
               "ADAOS_E2E_BUILDER_WEBSPACE": args.builder_webspace or "",
               "ADAOS_E2E_FIELD_TYPE": args.field_type or "",
               "ADAOS_E2E_SCENARIO_ID": created["scenario_id"],
               "ADAOS_E2E_WEBSPACE_ID": preview["webspace_id"],
               "ADAOS_E2E_SUBNET_ID": args.subnet,
               "ADAOS_E2E_LOCALE": value["context"]["locale"],
               "ADAOS_E2E_EMPTY_STATES": "1" if args.probe == "empty" else "0",
               "ADAOS_E2E_READONLY": "1" if args.probe == "readonly" else "0",
               "ADAOS_E2E_SELECT_WIDGET": collection["id"] if collection else "",
               "ADAOS_E2E_MEDIA_TESTS": environment.get("ADAOS_E2E_MEDIA_TESTS") or ("inspect" if details and details.get("inputs", {}).get("mediaKey") and collection else "0"),
               "ADAOS_E2E_OUTPUT": str(output.resolve())}
        print(f"[{case}] {args.probe} started", flush=True)
        result = subprocess.run(["node", str(Path(__file__).with_name(scripts[args.probe]))],
                                env=env, capture_output=True, text=True, encoding="utf-8")
        (output / "probe.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        summary.append({"case": case, "attempt": args.attempt, "probe": args.probe,
                        "exit_code": result.returncode, "output": str(output), "revisions": revisions,
                        "field_type": args.field_type,
                        "probe_digest": hashlib.sha256(Path(__file__).with_name(scripts[args.probe]).read_bytes()).hexdigest()})
        print(f"[{case}] {args.probe} exit={result.returncode}", flush=True)
    receipt = args.run / f"browser-{args.probe}" / f"cohort-{args.attempt:02}-{review_id}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(not summary or any(item["exit_code"] for item in summary))


if __name__ == "__main__":
    raise SystemExit(main())
