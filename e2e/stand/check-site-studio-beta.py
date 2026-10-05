"""Run the retained Site Studio beta journey against one explicit local surface."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess

from adaos.apps.cli.active_control import resolve_control_token


ROOT = Path(__file__).resolve().parents[2]
SCENARIO = "site_studio_test_20261003_e2eadc45d49732_26215830"
SKILL = f"{SCENARIO}_skill"
DEFAULT_IMAGE = (
    ROOT
    / "src/adaos/integrations/adaos-client/src/assets/public-sites/adaos/assets"
    / "builder-workbench-reference.png"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", default="http://127.0.0.1:8777")
    parser.add_argument("--client", default="http://127.0.0.1:8100/")
    parser.add_argument("--subnet", default="sn_6acf0c01")
    parser.add_argument("--webspace", default="desktop-codex-builder-dev")
    parser.add_argument(
        "--space-kind", choices=("development", "workspace"), default="development"
    )
    parser.add_argument(
        "--expected-runtime-source", choices=("dev", "trial", "workspace"), default="dev"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "e2e/artifacts/site-studio-beta-20261006/dev",
    )
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--require-collaboration-history", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    artifacts = (ROOT / "e2e/artifacts").resolve()
    if not output.is_relative_to(artifacts) or output == artifacts:
        parser.error("--output must be a child of e2e/artifacts")
    image = args.image.resolve()
    if not image.is_file():
        parser.error(f"verification image does not exist: {image}")

    environment = os.environ.copy()
    environment.update(
        {
            "ADAOS_E2E_HUB_URL": args.hub.rstrip("/"),
            "ADAOS_E2E_CLIENT_URL": args.client,
            "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=args.hub),
            "ADAOS_E2E_SUBNET_ID": args.subnet,
            "ADAOS_E2E_WEBSPACE_ID": args.webspace,
            "ADAOS_E2E_SCENARIO_ID": SCENARIO,
            "ADAOS_E2E_SKILL_ID": SKILL,
            "ADAOS_E2E_SPACE_KIND": args.space_kind,
            "ADAOS_E2E_EXPECTED_RUNTIME_SOURCE": args.expected_runtime_source,
            "ADAOS_E2E_REQUIRE_COLLABORATION_HISTORY": (
                "1" if args.require_collaboration_history else "0"
            ),
            "ADAOS_E2E_OUTPUT": str(output),
            "ADAOS_E2E_IMAGE": str(image),
        }
    )
    runner = ROOT / "e2e/stand/browser/site-studio-beta.mjs"
    completed = subprocess.run(
        ["node", str(runner)],
        cwd=runner.parent,
        env=environment,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
