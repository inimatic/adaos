"""Prepare tested DEV Builder runtimes and notify the local API before a cohort."""

import json
import os
from time import monotonic

from dotenv import load_dotenv

from adaos.apps.cli.app import Settings, init_ctx
from adaos.apps.cli.commands.skill import _notify_hub_skill_activated
from adaos.services.agent_context import get_ctx
from adaos.services.developer_project_validation import _manager


def main() -> None:
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        raise SystemExit("Requires ENV_TYPE=dev")
    init_ctx(Settings.from_sources())
    manager = _manager(get_ctx())
    for name in ("builder_skill", "builder_sdk_control_skill"):
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


if __name__ == "__main__":
    main()
