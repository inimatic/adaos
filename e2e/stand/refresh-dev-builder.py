"""Prepare tested DEV Builder runtimes and notify the local API before a cohort."""

import json
import argparse
import os
import hashlib
from time import monotonic

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.apps.cli.commands.skill import _notify_hub_skill_activated
from adaos.services.agent_context import get_ctx
from adaos.services.developer_project_validation import _manager


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", action="append", choices=("builder_skill", "builder_sdk_control_skill"))
    parser.add_argument("--ui", action="store_true", help="Materialize current Builder DEV UI in its existing paired Preview, without accepting a revision")
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        raise SystemExit("Requires ENV_TYPE=dev")
    init_ctx(Settings.from_sources())
    manager = _manager(get_ctx())
    for name in args.skill or ("builder_skill", "builder_sdk_control_skill"):
        started = monotonic()
        prepared = manager.prepare_dev_runtime(name, run_tests=True)
        tests = {key: value.status for key, value in (prepared.tests or {}).items()}
        print(json.dumps({"name": name, "stage": "prepared", "version": prepared.version,
                          "slot": prepared.slot, "tests": tests,
                          "elapsed_s": round(monotonic() - started, 2)}), flush=True)
        manager.activate_for_space(name, space="dev", version=prepared.version,
                                   slot=prepared.slot, webspace_id="desktop-dev", defer_webspace_rebuild=True)
        if not _notify_hub_skill_activated(name, space="dev", webspace_id="desktop-dev", defer_webspace_rebuild=True):
            raise RuntimeError(f"{name}: API activation notification failed")
        print(json.dumps({"name": name, "stage": "activated", "version": prepared.version,
                          "slot": prepared.slot, "elapsed_s": round(monotonic() - started, 2)}), flush=True)
    if args.ui:
        from adaos.sdk.builder import preview
        binding = preview.get_binding("desktop")
        if binding.get("runtime_scenario_id") != "builder" or binding.get("dev_webspace_id") != "desktop-dev":
            raise ValueError("Existing desktop Preview must already target Builder; this stand does not change selection")
        source = get_ctx().paths.dev_scenarios_dir() / "builder" / "webui.json"
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        started = monotonic()
        result = preview.materialize_revision_via_owner("desktop-dev", scenario_id="builder", preview_stage="prototype",
            preview_label="Builder DEV", source_fingerprint=digest,
            event_payload={"source_webspace_id": "desktop"}, timeout_s=120)
        if result.get("ok") is not True or result.get("accepted") is not True:
            raise RuntimeError("Current Builder UI was not materialized by its owner")
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Builder UI changed during materialization; review the current source before retry")
        print(json.dumps({"name": "builder", "stage": "ui_materialized", "source_sha256": digest,
            "identity": result.get("materialization_identity"), "elapsed_s": round(monotonic() - started, 2)}), flush=True)


if __name__ == "__main__":
    main()
