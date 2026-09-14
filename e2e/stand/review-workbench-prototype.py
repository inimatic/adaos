"""Reuse cohort browser probes for an application created by the live Builder stand."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from adaos.apps.cli.active_control import resolve_control_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creation", required=True, type=Path)
    parser.add_argument("--preview", required=True, type=Path)
    parser.add_argument("--probe", choices=("review", "interactions"), default="review")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Requires ENV_TYPE=dev")
    created = json.loads(args.creation.read_text(encoding="utf-8"))["created_test"]
    preview = json.loads(args.preview.read_text(encoding="utf-8"))
    identity = created["id"]
    if not identity.startswith("workbench_test_") or created["result"]["project"]["created_by"] != "builder.user":
        parser.error("Owned UI creation receipt required")
    target = parse_qs(urlparse(preview["url"]).query)
    if target.get("expected_scenario_id") != [identity] or target.get("webspace_id") != ["desktop-dev-dev"]:
        parser.error("Preview must match the single paired DEV Builder destination")
    source = Path(".adaos/dev/sn_6acf0c01/scenarios") / identity
    payload = json.loads((source / "webui.json").read_text(encoding="utf-8"))
    widgets = payload["ui"]["application"]["desktop"]["pageSchema"]["widgets"]
    collection = next((row for row in widgets if row["type"] in ("ui.list", "ui.table")), {})
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint = args.output / "ui-creation-checkpoint.json"
    checkpoint.write_text(json.dumps({
        "provenance": {"creation_receipt": str(args.creation), "preview_receipt": str(args.preview),
                       "source_sha256": hashlib.sha256((source / "webui.json").read_bytes()).hexdigest()},
        "context": {"locale": "ru"},
        "steps": [{"id": "create", "output": {"scenario_id": identity, "artifact_root": str(source.resolve())}}],
        "cleanup": {"test": True, "status": "retained_for_review", "acceptance": "not_approved",
                    "owned_artifacts": [{"primary_ref": "scenario:" + identity, "project_id": identity}],
                    "previews": [{"scenario_id": identity, "webspace_id": "desktop-dev-dev", "test": True, "stage": "prototype"}]},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    hub = "http://127.0.0.1:8778"
    environment = {**os.environ, "ADAOS_E2E_HUB_URL": hub,
                   "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=hub),
                   "ADAOS_E2E_SCENARIO_ID": identity, "ADAOS_E2E_WEBSPACE_ID": "desktop-dev-dev",
                   "ADAOS_E2E_SUBNET_ID": "sn_6acf0c01", "ADAOS_E2E_LOCALE": "ru",
                   "ADAOS_E2E_CHECKPOINT": str(checkpoint.resolve()), "ADAOS_E2E_SELECT_WIDGET": collection.get("id", ""),
                   "ADAOS_E2E_OUTPUT": str(args.output.resolve())}
    script = Path(__file__).with_name("browser") / f"prototype-{args.probe}.mjs"
    raise SystemExit(subprocess.run(["node", str(script)], env=environment).returncode)


if __name__ == "__main__":
    main()
