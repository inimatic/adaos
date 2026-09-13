"""Pin and inspect an existing test Prototype without regenerating or accepting it."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from dotenv import load_dotenv

from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.e2e.builder import _write_json
from adaos.e2e.stand import redact_value
from adaos.sdk.builder import preview, workflow
from adaos.sdk.developer import projects
from adaos.services.resources.prototype import prototype_webui_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
    parser.add_argument("output", type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--revision", default="002")
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev" or not args.host.startswith("e2e-"):
        parser.error("Requires a DEV node and an explicit isolated E2E Builder host")
    if not args.revision.isdigit():
        parser.error("Requires an exact numeric UI revision")
    root = (Path.cwd() / "e2e/artifacts/builder").resolve()
    output = args.output.resolve()
    if not output.is_relative_to(root) or output == root:
        parser.error("Evidence must stay inside e2e/artifacts/builder")
    init_ctx(Settings.from_sources())
    files = {}
    for name in ("webui.json", "semantic.webui.json", "builder.draft.json",
                 f"ui_revisions/{args.revision}.json"):
        result = projects.read_file("scenario", args.scenario, name, max_bytes=2_097_152)
        if result.get("truncated"):
            raise ValueError(f"Cannot pin a truncated source: {name}")
        files[name] = json.loads(result["content"])
    draft = files["builder.draft.json"]
    if "[TEST]" not in json.dumps(draft, ensure_ascii=False):
        raise ValueError("Existing scenario must be explicitly marked as a test")
    revision = files[f"ui_revisions/{args.revision}.json"]
    ui = revision["after_webui"]
    output.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        _write_json(output / "source" / name, content)
    _write_json(output / "prototype.webui.json", ui)
    _write_json(output / "workflow-before.json", workflow.get_state("scenario", args.scenario))
    receipt = {
        "scope": "existing Prototype comparison; no acceptance or Automation submission",
        "scenario_id": args.scenario, "revision": args.revision,
        "webui_digest": prototype_webui_digest(ui),
        "current_source_matches": prototype_webui_digest(files["webui.json"]) == prototype_webui_digest(ui),
        "source_digests": {name: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                           for name, value in files.items()},
        "core_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "client_commit": subprocess.check_output(["git", "-C", "src/adaos/integrations/adaos-client",
                                                   "rev-parse", "HEAD"], text=True).strip(),
    }
    _write_json(output / "pin.json", receipt)
    selected = preview.select_target("scenario", args.scenario, stage="prototype", revision=args.revision,
                                     source_webspace_id=args.host, via_owner=True)
    _write_json(output / "selection.json", redact_value(selected))
    if not selected.get("ok"):
        raise ValueError("Pinned Prototype materialization failed")
    hub = "http://127.0.0.1:8778"
    env = {**os.environ, "ADAOS_E2E_SCENARIO_ID": args.scenario,
           "ADAOS_E2E_WEBSPACE_ID": selected["preview_webspace_id"],
           "ADAOS_E2E_SUBNET_ID": args.subnet, "ADAOS_E2E_LOCALE": "ru",
           "ADAOS_E2E_HUB_URL": hub, "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=hub),
           "ADAOS_E2E_CLIENT_URL": "http://127.0.0.1:8100/", "ADAOS_E2E_REVIEW_STAGE": "prototype",
           "ADAOS_E2E_EXPECTED_WEBUI": str(output / "prototype.webui.json"),
           "ADAOS_E2E_OUTPUT": str(output / "browser")}
    result = subprocess.run(["node", str(Path(__file__).with_name("browser") / "prototype-review.mjs")],
                            env=env, capture_output=True, text=True, encoding="utf-8")
    (output / "browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    receipt.update(browser_exit_code=result.returncode, preview_webspace_id=selected["preview_webspace_id"])
    _write_json(output / "pin.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
