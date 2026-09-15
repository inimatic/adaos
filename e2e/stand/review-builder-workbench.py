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
    parser.add_argument("--refinement", action="store_true", help="Review Process, modal resizing and one README draft on the owned TEST")
    parser.add_argument("--modal-settings-only", action="store_true", help="Review shared modal preferences and explicit DEV defaults without another LLM request")
    parser.add_argument("--select-created", type=Path, help="Read-only review of the TEST application from a creation receipt")
    parser.add_argument("--trial-evidence", type=Path, help="Prepare only the reviewed TEST Trial from independent evidence")
    parser.add_argument("--open-trial", type=Path, help="Open the exact retained TEST Trial through Process")
    parser.add_argument("--accept-trial", action="store_true", help="Accept only that exact owned TEST Candidate into local Workspace from its changelog")
    parser.add_argument("--exercise-test", help="Create one new workbench_test_* application through the UI")
    parser.add_argument("--resume-created", type=Path, help="Prior report proving this stand created the TEST application")
    parser.add_argument("--prototype-prompt", type=Path, help="Submit one explicit prompt through the created TEST application's chat")
    parser.add_argument("--base-revision", help="Expected current revision for one deliberate follow-up")
    parser.add_argument("--open-preview", action="store_true", help="Open the selected owned TEST preview through Builder")
    parser.add_argument("--verify-existing-preview", action="store_true", help="Open an existing owned TEST and verify the Result target")
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
    if args.accept_trial:
        if not args.open_trial or args.refinement:
            parser.error("Acceptance requires an exact Trial receipt and no competing refinement")
        env["ADAOS_E2E_ACCEPT_TRIAL"] = "1"
    if args.refinement:
        if not args.select_created or not args.inspect:
            parser.error("Refinement review requires an owned TEST selection and inspect mode")
        env["ADAOS_E2E_REFINEMENT"] = "1"
    if args.modal_settings_only:
        if not args.refinement:
            parser.error("Modal preference review requires refinement mode")
        env["ADAOS_E2E_MODAL_SETTINGS_ONLY"] = "1"
    if args.select_created:
        import json
        receipt = json.loads(args.select_created.read_text(encoding="utf-8"))["created_test"]
        if args.exercise_test or not receipt["id"].startswith("workbench_test_") or receipt["result"]["project"]["created_by"] != "builder.user":
            parser.error("Read-only selection requires an owned TEST receipt and no exercise")
        env["ADAOS_E2E_SELECT_CREATED"] = json.dumps(receipt, ensure_ascii=False)
    if args.trial_evidence:
        import hashlib
        import json
        if not args.select_created or not args.inspect:
            parser.error("Trial review requires owned selection in inspect mode")
        root = args.trial_evidence.resolve()
        session = json.loads(Path(f".adaos/state/builder/automation/scenario.{receipt['id']}.json").read_text(encoding="utf-8"))
        raw = Path(f".adaos/dev/sn_6acf0c01/scenarios/{receipt['id']}/webui.json").read_bytes()
        reports = []
        for name in ("automation-http-01.json", "automation-browser-02.json", "automation-restart-02.json"):
            path = root / name
            evidence = json.loads(path.read_text(encoding="utf-8"))
            if evidence.get("passed") is not True or evidence.get("scenario") != receipt["id"] or evidence.get("task") != session["current_task_id"] or evidence.get("source_sha256") != hashlib.sha256(raw).hexdigest():
                parser.error("Current independent HTTP, browser and restart evidence required")
            reports.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        env["ADAOS_E2E_PREPARE_TRIAL"] = json.dumps({"scenario": receipt["id"], "task": session["current_task_id"],
            "reviewer": {"id": "agent:codex-independent-test-review", "kind": "agent", "delegated_by": "user:local"},
            "scope": "Explicitly delegated TEST Trial preparation, not human or stable/publication acceptance", "reports": reports}, ensure_ascii=False)
    if args.open_trial:
        import json
        trial = json.loads(args.open_trial.read_text(encoding="utf-8"))
        if trial.get("builder_placement"):
            placement = trial["builder_placement"]
            trial = {"passed": trial["passed"], "scenario": placement["scenario_id"],
                     "placement": placement, "delivery": {
                         "candidate_id": placement["result_ref"]["id"],
                         "release_digest": trial["placement"]["runtime_selection"]["release_digest"]}}
        if not args.select_created or not args.inspect or args.trial_evidence or trial.get("passed") is not True or trial["scenario"] != receipt["id"]:
            parser.error("Exact TEST Trial receipt and inspect-only selection required")
        env["ADAOS_E2E_OPEN_TRIAL"] = json.dumps(trial, ensure_ascii=False)
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
    if args.verify_existing_preview:
        if not args.select_created or not args.inspect:
            parser.error("Existing Preview verification requires owned TEST selection and inspect mode")
        env["ADAOS_E2E_VERIFY_EXISTING_PREVIEW"] = "1"
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
