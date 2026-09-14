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
    parser.add_argument("--resume-created", type=Path, help="Prior report proving this stand created the TEST application")
    parser.add_argument("--prototype-prompt", type=Path, help="Submit one explicit prompt through the created TEST application's chat")
    parser.add_argument("--base-revision", help="Expected current revision for one deliberate follow-up")
    parser.add_argument("--open-preview", action="store_true", help="Open the selected owned TEST preview through Builder")
    parser.add_argument("--automation-brief", type=Path, help="Start one explicitly accepted owned TEST through the native form")
    parser.add_argument("--automation-followup", action="store_true", help="Use the existing Automation iteration form")
    parser.add_argument("--codex-model", default="gpt-5.5")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Requires ENV_TYPE=dev")
    hub = "http://127.0.0.1:8778"
    env = {**os.environ, "ADAOS_E2E_HUB_URL": hub,
           "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=hub),
           "ADAOS_E2E_OUTPUT": str(args.output.resolve())}
    env["ADAOS_E2E_CODEX_MODEL"] = args.codex_model
    if args.automation_followup:
        if not args.automation_brief:
            parser.error("An explicit follow-up brief is required")
        env["ADAOS_E2E_AUTOMATION_FOLLOWUP"] = "1"
    if args.resume_created:
        import json
        receipt = json.loads(args.resume_created.read_text(encoding="utf-8"))["created_test"]
        if receipt["id"] != args.exercise_test or receipt["result"]["project"]["created_by"] != "builder.user":
            parser.error("Resume must match the exact UI creation receipt")
        env["ADAOS_E2E_CREATED_TEST"] = json.dumps(receipt, ensure_ascii=False)
    if args.prototype_prompt:
        if not args.exercise_test:
            parser.error("A prototype prompt requires the owned TEST exercise")
        env["ADAOS_E2E_PROTOTYPE_PROMPT"] = args.prototype_prompt.read_text(encoding="utf-8").strip()
    if args.base_revision:
        if not args.resume_created or not args.prototype_prompt:
            parser.error("A follow-up requires both creation provenance and an explicit prompt")
        env["ADAOS_E2E_BASE_REVISION"] = args.base_revision
    if args.open_preview:
        if not args.exercise_test:
            parser.error("Preview review requires the owned TEST exercise")
        env["ADAOS_E2E_OPEN_PREVIEW"] = "1"
    if args.automation_brief:
        if not args.resume_created or args.prototype_prompt or args.open_preview:
            parser.error("Automation requires prior creation and no competing Prototype/preview operation")
        env["ADAOS_E2E_AUTOMATION_BRIEF"] = args.automation_brief.read_text(encoding="utf-8").strip()
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
