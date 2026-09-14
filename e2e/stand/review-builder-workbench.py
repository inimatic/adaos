"""Read-only live Builder UI review on the local DEV node."""

import argparse
import os
from pathlib import Path
import subprocess

from dotenv import load_dotenv

from adaos.apps.cli.active_control import resolve_control_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--exercise-test", help="Create one new workbench_test_* application through the UI")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Requires ENV_TYPE=dev")
    hub = "http://127.0.0.1:8778"
    env = {**os.environ, "ADAOS_E2E_HUB_URL": hub,
           "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=hub),
           "ADAOS_E2E_OUTPUT": str(args.output.resolve())}
    command = ["node", str(Path(__file__).with_name("browser") / "builder-workbench-live.mjs")]
    if args.inspect:
        command.append("--inspect")
    if args.exercise_test:
        if not args.exercise_test.startswith("workbench_test_"):
            parser.error("Mutation checks require a new workbench_test_* ID")
        command.extend(["--exercise-test", args.exercise_test])
    raise SystemExit(subprocess.run(command, env=env).returncode)


if __name__ == "__main__":
    main()
