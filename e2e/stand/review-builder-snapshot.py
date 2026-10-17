"""Inspect a retained test Prototype or Automation without regeneration or acceptance."""

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
from adaos.sdk.developer import compositions, projects
from adaos.services.resources.prototype import prototype_webui_digest
from adaos.services.builder.workflow import BuilderWorkflowService


def read_automation_snapshot(snapshot: Path, scenario: str, revision: str):
    metadata = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    if (metadata.get("object_type") != "scenario" or metadata.get("object_id") != scenario
            or metadata.get("task_id") != revision or not revision.startswith("task.")):
        raise ValueError("Automation review requires the exact retained task revision")
    ui = json.loads((snapshot / "webui.json").read_text(encoding="utf-8"))
    if not isinstance(ui.get("ui", {}).get("application"), dict):
        raise ValueError("Automation snapshot has no application UI")
    return metadata, ui


def require_test_ownership(scenario, draft, project=None):
    if "[TEST]" in json.dumps(draft, ensure_ascii=False):
        return
    project = project or {}
    catalog = project.get("catalog") or {}
    owned = (project.get("components") or {}).get("owned") or []
    if (not scenario.startswith("test_") or "[TEST]" not in str(catalog.get("title") or "")
            or "test" not in catalog.get("tags", [])
            or not any(item.get("ref") == f"scenario:{scenario}" for item in owned)):
        raise ValueError("Existing scenario must be explicitly marked as an owned test")


def require_completed_candidate(task, *, scenario, revision):
    if (task.get('task_id') != revision or task.get('status') != 'completed'
            or task.get('target') != {'type':'scenario', 'id':scenario}):
        raise ValueError('Candidate observation requires the exact completed Factory task for this scenario')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
    parser.add_argument("output", type=Path)
    parser.add_argument("--host", required=True)
    parser.add_argument("--subnet", required=True)
    parser.add_argument("--revision", default="002")
    parser.add_argument("--locale", choices=("en", "ru"), default="ru")
    parser.add_argument("--stage", choices=("prototype", "automation"), default="prototype")
    parser.add_argument("--probe", choices=("review", "equipment-journey", "equipment-repeat", "application-journey"), default="review")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--profile", choices=("knowledge-library.v1",))
    parser.add_argument("--client-url", default="http://127.0.0.1:8100/")
    parser.add_argument('--candidate-qualification', action='store_true',
                        help='Observe an exact completed TEST Factory candidate; does not accept its workflow')
    args = parser.parse_args()
    load_dotenv()
    if os.getenv("ENV_TYPE") != "dev":
        parser.error("Requires a DEV node")
    if args.stage == "prototype" and not args.revision.isdigit():
        parser.error("Requires an exact numeric UI revision")
    if args.probe != "review" and args.stage != "automation":
        parser.error("Functional journeys require an Automation snapshot")
    if args.profile and args.probe != 'application-journey':
        parser.error('Registered plan qualification requires the application journey probe')
    if args.candidate_qualification and (not args.profile or args.stage != 'automation'):
        parser.error('Candidate observation requires an explicit TEST application profile and Automation snapshot')
    if args.probe == "application-journey":
        if not args.plan or not args.checkpoint:
            parser.error("Application journeys require a plan and TEST ownership checkpoint")
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        if (plan.get("schema") != "adaos.e2e.application_journey.v1" or not plan.get("steps")
                or not checkpoint.get("cleanup", {}).get("test")
                or not checkpoint.get("context", {}).get("retain_test_projects")
                or not any(item.get("primary_ref") == f"scenario:{args.scenario}"
                           for item in checkpoint["context"].get("owned_artifacts", []))):
            parser.error("Invalid journey or TEST ownership")
        if args.profile:
            from adaos.services.builder.journey_profiles import profile_definition
            definition = profile_definition(repo_root=Path.cwd(), profile_id=args.profile)
            if plan != definition['plan']:
                parser.error('A registered profile requires its exact platform-owned acceptance plan')
    root = (Path.cwd() / "e2e/artifacts/builder").resolve()
    output = args.output.resolve()
    if not output.is_relative_to(root) or output == root:
        parser.error("Evidence must stay inside e2e/artifacts/builder")
    init_ctx(Settings.from_sources())
    from adaos.e2e.builder_host import require_builder_host

    require_builder_host(args.host)
    files = {}
    names = ["webui.json", "semantic.webui.json", "builder.draft.json"]
    if args.stage == "prototype":
        names.append(f"ui_revisions/{args.revision}.json")
    for name in names:
        result = projects.read_file("scenario", args.scenario, name, max_bytes=2_097_152)
        if result.get("truncated"):
            raise ValueError(f"Cannot pin a truncated source: {name}")
        files[name] = json.loads(result["content"])
    draft = files["builder.draft.json"]
    project = None
    if "[TEST]" not in json.dumps(draft, ensure_ascii=False):
        project = compositions.get(args.scenario)
    require_test_ownership(args.scenario, draft, project)
    state = workflow.get_state("scenario", args.scenario)
    if args.stage == "prototype":
        ui = files[f"ui_revisions/{args.revision}.json"]["after_webui"]
    else:
        snapshot = BuilderWorkflowService.from_context().automation_snapshot_root("scenario", args.scenario)
        metadata, ui = read_automation_snapshot(snapshot, args.scenario, args.revision)
        files["automation-snapshot.json"] = metadata
    profile_sources_before = None
    profile_verifier_before = None
    if args.profile:
        from adaos.services.builder.browser_feedback import _implementation_snapshot
        workflow_owner = BuilderWorkflowService.from_context()
        profile_sources = [workflow_owner.dev_scenarios_root / args.scenario]
        profile_sources.extend(workflow_owner.dev_skills_root / row['ref'].split(':',1)[1]
            for row in metadata.get('components', []) if row.get('ref', '').startswith('skill:'))
        profile_sources_before = _implementation_snapshot(profile_sources)
        profile_verifier_paths = [Path(__file__), Path('e2e/stand/browser/application-journey.mjs'),
            Path('e2e/stand/browser/application-navigation.mjs'),
            Path(definition['profile']['plan']), Path('src/adaos/services/builder/journey_profiles.py')]
        def verifier_snapshot():
            return {str(path): 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in profile_verifier_paths}
        profile_verifier_before = verifier_snapshot()
    output.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        _write_json(output / "source" / name, content)
    ui_path = output / f"{args.stage}.webui.json"
    _write_json(ui_path, ui)
    _write_json(output / "workflow-before.json", state)
    receipt = {
        "scope": "existing snapshot comparison; no acceptance or Automation submission",
        "stage": args.stage,
        "scenario_id": args.scenario, "revision": args.revision,
        "webui_digest": prototype_webui_digest(ui),
        "current_source_matches": prototype_webui_digest(files["webui.json"]) == prototype_webui_digest(ui),
        "current_application_matches": files["webui.json"].get("ui", {}).get("application") == ui.get("ui", {}).get("application"),
        "source_digests": {name: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                           for name, value in files.items()},
        "core_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "client_commit": subprocess.check_output(["git", "-C", "src/adaos/integrations/adaos-client",
                                                   "rev-parse", "HEAD"], text=True).strip(),
    }
    _write_json(output / "pin.json", receipt)
    candidate_owner = None
    candidate_materialization = None
    if args.candidate_qualification:
        from adaos.services.builder.automation import BuilderAutomationService
        candidate_owner = BuilderAutomationService.from_context()
        require_completed_candidate(candidate_owner.factory.read_task(args.revision),
                                    scenario=args.scenario, revision=args.revision)
        candidate_materialization = candidate_owner._materialize_candidate_for_browser_feedback(
            {'object_type':'scenario','object_id':args.scenario,'current_task_id':args.revision},
            webspace_id=args.host)
        selected = {'ok':candidate_materialization['ok'],
                    'preview_webspace_id':candidate_materialization['preview_webspace_id'],
                    'materialization':candidate_materialization}
    else:
        selected = preview.select_target("scenario", args.scenario, stage=args.stage, revision=args.revision,
                                         source_webspace_id=args.host, via_owner=True)
    _write_json(output / "selection.json", redact_value(selected))
    if not selected.get("ok"):
        raise ValueError("Pinned snapshot materialization failed")
    receipt["preview_webspace_id"] = selected["preview_webspace_id"]
    if args.profile:
        from adaos.services.builder.journey_profiles import require_owned_plan_target
        identity = selected.get('materialization', {}).get('materialization_identity') or {}
        if identity.get('revision') != args.revision or not identity.get('source_fingerprint'):
            raise ValueError('The materialization owner did not provide an exact source fingerprint')
        receipt['expected_materialization'] = {key: identity[key] for key in ('revision', 'source_fingerprint')}
        require_owned_plan_target(profile_id=args.profile, scenario_id=args.scenario, task_id=args.revision,
            webspace_id=selected['preview_webspace_id'], checkpoint=checkpoint, pin=receipt)
    _write_json(output / "pin.json", receipt)
    hub = "http://127.0.0.1:8777"
    env = {**os.environ, "ADAOS_E2E_SCENARIO_ID": args.scenario,
           "ADAOS_E2E_WEBSPACE_ID": selected["preview_webspace_id"],
           "ADAOS_E2E_SUBNET_ID": args.subnet, "ADAOS_E2E_LOCALE": args.locale,
           "ADAOS_E2E_HUB_URL": hub, "ADAOS_E2E_HUB_TOKEN": resolve_control_token(base_url=hub),
           "ADAOS_E2E_CLIENT_URL": args.client_url, "ADAOS_E2E_REVIEW_STAGE": args.stage,
           "ADAOS_E2E_EXPECTED_WEBUI": str(ui_path),
           "ADAOS_E2E_SNAPSHOT_PIN": str(output / "pin.json"),
           "ADAOS_E2E_OUTPUT": str(output / "browser")}
    if args.probe == "application-journey":
        env.update(ADAOS_E2E_JOURNEY_PLAN=str(args.plan.resolve()),
                   ADAOS_E2E_CHECKPOINT=str(args.checkpoint.resolve()))
    script = {"review": "prototype-review.mjs", "equipment-journey": "equipment-automation.mjs",
              "equipment-repeat": "equipment-repeat.mjs", "application-journey": "application-journey.mjs"}[args.probe]
    try:
        result = subprocess.run(["node", str(Path(__file__).with_name("browser") / script)],
                                env=env, capture_output=True, text=True, encoding="utf-8")
    finally:
        if candidate_materialization and candidate_materialization.get('temporary_preview_override'):
            restored = candidate_owner._restore_preview_after_browser_feedback(candidate_materialization)
            _write_json(output/'preview-restoration.json', redact_value(restored))
    (output / "browser.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    receipt.update(browser_exit_code=result.returncode, preview_webspace_id=selected["preview_webspace_id"])
    if args.profile:
        from adaos.services.builder.journey_profiles import qualify_owned_plan_report
        report = json.loads((output / 'browser/journey.json').read_text(encoding='utf-8'))
        after = _implementation_snapshot(profile_sources)
        verifier_after = verifier_snapshot()
        qualification = qualify_owned_plan_report(repo_root=Path.cwd(), profile_id=args.profile,
            report=report, pin=receipt, source_unchanged=after == profile_sources_before,
            verifier_unchanged=verifier_after == profile_verifier_before)
        qualification.update(task_id=args.revision, scenario_id=args.scenario,
            implementation_before=profile_sources_before, implementation_after=after,
            verifier_before=profile_verifier_before, verifier_after=verifier_after,
            pin=receipt, new_provider_submissions=0,
            workflow_acceptance='excluded; candidate observation' if args.candidate_qualification else 'existing preview')
        _write_json(output / 'registered-profile-receipt.json', qualification)
        if not qualification['passed']:
            result.returncode = 1
    _write_json(output / "pin.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
