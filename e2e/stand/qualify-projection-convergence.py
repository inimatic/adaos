"""Qualify same-tab navigation against exact locally selected Applications."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import time

import requests

from adaos.apps.cli.active_control import resolve_control_token
from adaos.apps.cli.app import Settings, init_ctx
from adaos.sdk import applications
from adaos.services.agent_context import get_ctx
from adaos.services.applications.store import ApplicationStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", default="http://127.0.0.1:8777")
    parser.add_argument("--client", default="http://127.0.0.1:8100")
    parser.add_argument("--subnet-id", required=True)
    parser.add_argument("--webspace", default="desktop-codex-projection-convergence-20261027")
    parser.add_argument("--transitions", type=int, default=50)
    parser.add_argument("--idle-seconds", type=int, default=900)
    parser.add_argument("--output", default="e2e/artifacts/runtime-projection-convergence")
    args = parser.parse_args()
    if not args.webspace.startswith("desktop-codex-"):
        parser.error("an isolated desktop-codex- Webspace is required")
    logging.disable(logging.CRITICAL)
    init_ctx(Settings.from_sources())
    ctx = get_ctx()
    store = ApplicationStore(Path(ctx.paths.state_dir()))
    session = requests.Session()
    session.trust_env = False
    token = resolve_control_token(base_url=args.hub)
    session.headers["X-AdaOS-Token"] = token
    deadline = time.monotonic() + 120
    while True:
        try:
            response = session.get(args.hub + "/api/node/yjs/webspaces", timeout=30)
            break
        except requests.ConnectionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    response.raise_for_status()
    if args.webspace not in {item["id"] for item in response.json()["items"]}:
        response = session.post(args.hub + "/api/node/yjs/webspaces", json={
            "id": args.webspace, "title": "Projection convergence qualification", "scenario_id": "web_desktop", "dev": False,
        }, timeout=120)
        response.raise_for_status()
    cases = []
    for app_id, scenario in [("web_desktop", "web_desktop"), ("applications", "applications"),
                             ("adaos_builder", "builder"), ("context_chat", "context_chat")]:
        selected = store.get_runtime_selection("desktop", app_id)
        try:
            current = store.get_runtime_selection(args.webspace, app_id)
        except FileNotFoundError:
            current = None
        if current is None or current.release_digest != selected.release_digest:
            applications.select_runtime(webspace_id=args.webspace, application_id=app_id,
                source=selected.source, release_digest=selected.release_digest, runtime_root_ref=selected.runtime_root_ref,
                expected_revision=current.revision if current else 0, actor_ref="codex.projection.acceptance",
                subnet_ref=f"subnet:{args.subnet_id}",
                capability="applications.apply")
        cases.append({"application_id": app_id, "scenario_id": scenario, "release_digest": selected.release_digest})
    env = {**os.environ, "ADAOS_E2E_API": args.hub, "ADAOS_E2E_CLIENT": args.client,
           "ADAOS_E2E_HUB_TOKEN": token, "ADAOS_E2E_WEBSPACE": args.webspace,
           "ADAOS_E2E_SUBNET_ID": args.subnet_id,
           "ADAOS_E2E_ARTIFACT_DIR": str(Path(args.output).resolve()),
           "ADAOS_E2E_TRANSITIONS": str(args.transitions), "ADAOS_E2E_IDLE_SECONDS": str(args.idle_seconds),
           "ADAOS_E2E_PROJECTION_CASES": json.dumps(cases)}
    return subprocess.call(["node", "e2e/stand/browser/runtime-projection-convergence.mjs"], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
