"""Trusted, bounded Application journeys used by the existing browser gate.

Profiles are Core-owned code, never a runner path supplied by a candidate.
The first profile qualifies Retouch mechanics with a no-provider boundary.
It deliberately cannot accept image quality or live Root admission.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.log_redaction import redact_log_text
from .traceability import project_change, make_behavioral_receipt, attach_behavioral_execution


RETOUCH_OBLIGATIONS = {
    "loading-error-empty": "view:v_photos",
    "upload-jpeg-lossless": "command:cmd_add_photo",
    "local-draft-preservation": "view:d_photo_editor",
    "goal-parameters-no-submit": "view:e_edit_params",
    "rectangle-protection": "view:d_photo_editor",
    "budget-admission-failure-visible": "view:e_edit_params",
    "analysis-one-submit-editable-plan": "view:e_edit_params",
    "approval-progress-reopen-idempotence": "view:d_photo_editor",
    "completed-run-cost-authority": "view:d_photo_editor",
    "compare-zoom-pan-difference-download": "view:d_result_preview",
    "adopt-incremented-original": "view:v_results",
    "unknown-cancel-reservation-isolation": "view:d_photo_editor",
    "completed-mask-reanalysis": "view:d_photo_editor",
    "result-input-history": "view:v_results",
}
RETOUCH_CASES = ('exact/wide', 'exact/compact', 'edited/wide', 'edited/compact')
COST_CASES = ('measured-current-run', 'other-run-isolation',
              'other-run-unknown-must-not-hide-known-current-cost', 'partial-confirmed-with-unknown')


def qualify_cost_report(report, *, source_digest):
    cases = report.get('cases') or []
    attempts = report.get('attempts') or []
    def amount(value): return type(value) in (int, float) and math.isfinite(value) and value >= 0
    metered = (len(attempts) == 3 and bool(report.get('run_id'))
        and len({row.get('request_id') for row in attempts}) == 3
        and all(row.get('run_id') == report.get('run_id') and row.get('status') == 'reconciled'
                and row.get('cost_basis') == 'metered' and amount(row.get('observed_usd')) for row in attempts))
    total = sum(row.get('observed_usd', 0) for row in attempts) if metered else None
    def observed(row):
        value = row.get('observed_known_usd') if row.get('id') == COST_CASES[-1] else row.get('observed_usd')
        valid = amount(value) and total is not None and abs(value - total) < 1e-9
        return valid and (row.get('id') != COST_CASES[-1] or 'confirmed spend: unknown' in row.get('text', ''))
    complete = (report.get('completed') is True and report.get('source_digest') == source_digest
        and bool(source_digest) and report.get('external_model_calls') == 0
        and all(report.get('environment', {}).get(k) for k in ('python', 'platform', 'pillow', 'numpy'))
        and len(cases) == len(COST_CASES) and {row.get('id') for row in cases} == set(COST_CASES)
        and all(type(row.get('passed')) is bool for row in cases) and metered
        and all(row.get('passed') == observed(row) for row in cases)
        and report.get('passed') == all(row.get('passed') is True for row in cases))
    failures = [{'layout': 'cost-authority', 'active': row.get('id'),
                 'failure': 'Selected-run SDK metering is not faithfully displayed: ' + str(row.get('text') or '')[:900]}
                for row in cases if row.get('passed') is not True]
    ok = complete and report.get('passed') is True and not failures
    if not complete:
        failures.append({'layout': 'cost-authority', 'failure': 'Cost verifier dependencies or cases incomplete'})
    return {'ok': bool(ok), 'repairable': bool(complete and not ok), 'failures': failures}


def file_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_owned_journey(command, *, cwd, env, timeout, **options):
    """Use the existing DEV process-family owner for timeout cleanup."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        from adaos.services.developer_project_validation import _terminate_process_tree
        _terminate_process_tree(process)
        stdout, stderr = process.communicate(timeout=10)
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def qualify_reports(reports, *, expected_materialization, source_digest):
    """Fail closed on a green runner with missing postconditions or dependencies."""
    failures, repairable_failures = [], []
    for layout in RETOUCH_CASES:
        report = reports.get(layout) or {}
        checks = report.get("checks") or []
        present = {row.get("id") for row in checks if row.get("passed") is True}
        identity_ok = bool(expected_materialization) and bool(report.get("observed_materializations")) and all(
            all(observed.get(key) == value for key, value in expected_materialization.items())
            for observed in report.get("observed_materializations") or [])
        source_ok = bool(source_digest) and report.get("specification", {}).get("source_digest") == source_digest
        assets = report.get("client_assets") or []
        expected_variant, expected_layout = layout.split('/')
        dependencies_ok = (identity_ok and source_ok and bool(assets)
                           and report.get('specification', {}).get('variant') == expected_variant
                           and report.get('specification', {}).get('layout') == expected_layout
                           and all(row.get('digest') and row.get('size_bytes', 0) > 0 for row in assets)
                           and report.get("final_audit", {}).get("source_digest") == source_digest
                           and bool(report.get('browser_environment', {}).get('browser_version'))
                           and bool(report.get('browser_environment', {}).get('viewport'))
                           and all(report.get('final_audit', {}).get('environment', {}).get(key)
                                   for key in ('python', 'platform', 'pillow', 'numpy'))
                           and not report.get("errors"))
        complete = present == set(RETOUCH_OBLIGATIONS) and len(checks) == len(RETOUCH_OBLIGATIONS)
        passed = (report.get("passed") is True and dependencies_ok and complete
                  and set(report.get('specification', {}).get('obligations', [])) == set(RETOUCH_OBLIGATIONS)
                  and not report.get("failure") and not report.get("supervisor_interventions")
                  and report.get("independent_pixel_verification", {}).get("passed") is True
                  and report.get("final_audit", {}).get("external_model_calls") == 0)
        if not passed:
            missing = sorted(set(RETOUCH_OBLIGATIONS) - present)
            failures.append({"layout": layout, "active": report.get("active"),
                "failure": report.get("failure") or "application_journey_incomplete",
                "missing_obligations": missing, "dependencies_complete": dependencies_ok})
            # Assertion on a healthy, exact candidate is a bounded observed
            # postcondition failure. An arbitrary timeout/runner error is not.
            repairable_failures.append(bool(dependencies_ok and report.get("failure_kind") == "candidate_postcondition"
                               and report.get("active") in RETOUCH_OBLIGATIONS))
    return {"ok": not failures, "failures": failures,
            "repairable": bool(repairable_failures) and all(repairable_failures)}


def evaluate_retouch(*, repo_root, scenario_id, task_id, implementation_paths,
                     environment, implementation_snapshot, expected_materialization,
                     change=None, semantic_document=None, source_message_ids=(), snapshot_reader=None, command_runner=subprocess.run):
    """Run only the registered profile through the existing finalization owner."""
    if scenario_id != "natural_retouch":
        return None
    sources = [Path(path) for path in implementation_paths
               if Path(path).name == "natural_retouch_skill" and (Path(path) / "handlers/retouch.py").is_file()]
    if len(sources) != 1 or not expected_materialization:
        return {"ok": False, "profile": "natural-retouch.v4", "repairable": False,
                "failures": [{"failure": "trusted_application_source_or_materialization_missing"}]}
    repo = Path(repo_root).resolve()
    label = "builder-retouch-" + uuid.uuid4().hex
    output = repo / "e2e/artifacts/builder-application-journeys" / label
    output.mkdir(parents=True, exist_ok=False)
    specs = [{"constraint_id": "retouch." + identity, "kind": "browser_postcondition", "target_ref": target,
              "expected": identity, "scope": "owned synthetic provider boundary; live quality excluded",
              "authority": "trusted_application_journey_profile", "requirement_refs": ["retouch.journey." + identity]}
             for identity, target in RETOUCH_OBLIGATIONS.items()]
    specs.extend({'constraint_id': 'retouch.cost.' + identity, 'kind': 'owned_handler_cost_postcondition',
        'target_ref': 'view:d_photo_editor', 'expected': identity,
        'authority': 'trusted_application_cost_projection_profile', 'requirement_refs': ['retouch.requirement.cost.' + identity]}
        for identity in COST_CASES)
    specs.append({"constraint_id": "retouch.execution_integrity", "kind": "execution_owner_postcondition",
                  "authority": "trusted_application_journey_profile",
                  "expected": "runner_zero_exit_and_complete_unassisted_qualification"})
    atomic_write_json(output / "verification-spec.json", {"specifications": specs,
                      "cases": list(RETOUCH_CASES),
                      "expected_materialization": dict(expected_materialization)})
    runner = repo / "e2e/stand/qualify-retouch-browser.py"
    trusted_files = [runner, repo / 'e2e/stand/qualify-retouch-cost.py', repo / 'e2e/stand/serve-retouch-fixture.py',
        repo / 'e2e/stand/browser/retouch-journey.mjs', repo / 'src/adaos/e2e/retouch_fixture.py',
        repo / 'e2e/stand/browser/controlled-faults.mjs', repo / 'src/adaos/e2e/retouch_verification.py',
        repo / 'src/adaos/services/builder/spend_ledger.py', repo / 'src/adaos/sdk/llm/errors.py', Path(__file__)]
    trusted_closure = {str(path.relative_to(repo)).replace('\\', '/'): file_digest(path)
                       for path in trusted_files if path.is_relative_to(repo)}
    env = {**environment, "ADAOS_E2E_EXPECTED_MATERIALIZATION": json.dumps(expected_materialization)}
    timed_out = False
    try:
        if command_runner is subprocess.run:
            command_runner = run_owned_journey
        completed = command_runner([sys.executable, "-X", "utf8", str(runner), "--source", str(sources[0]),
            "--output", str(output), "--label", label], cwd=repo, env=env, capture_output=True,
            text=True, encoding="utf-8", check=False, timeout=1200)
        exit_code = int(completed.returncode)
        log = str(completed.stdout or "") + str(completed.stderr or "")
    except subprocess.TimeoutExpired:
        exit_code, log, timed_out = 124, "Application browser journey timed out", True
    (output / "browser.log").write_text(redact_log_text(log), encoding="utf-8")
    reports = {}
    for layout in RETOUCH_CASES:
        try:
            reports[layout] = json.loads((output / layout / "journey.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reports[layout] = {}
    # Same digest algorithm as the fixture, computed independently by the owner.
    source_digest = "sha256:" + hashlib.sha256(json.dumps({p.name: file_digest(p)
        for p in sorted((sources[0] / "handlers").glob("*.py"))}, sort_keys=True).encode()).hexdigest()
    try:
        cost_report = json.loads((output / 'cost-authority/report.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        cost_report = {}
    browser_outcome = qualify_reports(reports, expected_materialization=expected_materialization, source_digest=source_digest)
    cost_outcome = qualify_cost_report(cost_report, source_digest=source_digest)
    receipt = {"schema": "adaos.builder.application_journey_receipt.v1", "profile": "natural-retouch.v4",
               "scope": "Client + real handlers/SQLite/CPU; synthetic Broker and blob/access boundaries. Live provider quality and admission excluded.",
               "task_id": task_id, "reports": reports, "exit_code": exit_code, "timed_out": timed_out,
               'cost_authority': cost_report,
               'ok': browser_outcome['ok'] and cost_outcome['ok'],
               'repairable': (not (browser_outcome['ok'] and cost_outcome['ok'])
                   and (browser_outcome['ok'] or browser_outcome['repairable'])
                   and (cost_outcome['ok'] or cost_outcome['repairable'])),
               'failures': [*browser_outcome['failures'], *cost_outcome['failures']]}
    receipt["ok"] = receipt["ok"] and exit_code == 0 and not timed_out
    receipt["repairable"] = receipt["repairable"] and not timed_out
    candidate_unchanged = snapshot_reader is not None and snapshot_reader() == implementation_snapshot
    trusted_unchanged = all(file_digest(repo / name) == value for name, value in trusted_closure.items())
    unchanged = candidate_unchanged and trusted_unchanged
    if not unchanged:
        receipt.update(ok=False, repairable=False)
        if not candidate_unchanged:
            receipt["failures"].append({"failure": "candidate_changed_during_application_journey"})
        if not trusted_unchanged:
            receipt["failures"].append({"failure": "trusted_verifier_changed_during_application_journey"})
    state = {'browser': [{"layout": layout, "initial_checks": row.get("checks"), "final_audit": row.get("final_audit")}
             for layout, row in reports.items()], 'cost_authority': cost_report}
    context = {"implementation": implementation_snapshot,
               "runtime": {"expected_materialization": expected_materialization,
                           "source_digest": source_digest,
                           "clients": {layout: row.get("client_assets") for layout, row in reports.items()},
                           "runner_closure": trusted_closure,
                           "observed": {layout: {key: (row.get('runtime') or {}).get(key)
                               for key in ('buildId', 'zone', 'rootBase', 'hubBase', 'subnetId')}
                               for layout, row in reports.items()}},
               "state": state,
               "environment": {"cases": list(RETOUCH_CASES),
                               "observed": {layout: {'browser': row.get('browser_environment'),
                                   'fixture': row.get('final_audit', {}).get('environment')} for layout, row in reports.items()},
                               'cost_authority': cost_report.get('environment'),
                               "fixture": "loopback-only; external connect denied",
                               "hub": env.get("ADAOS_E2E_HUB_URL"), "client": env.get("ADAOS_E2E_CLIENT_URL")}}
    graph = project_change(project_ref="scenario:" + scenario_id, generation=1,
        change={**(change or {}), "issues": [*(change or {}).get('issues', []),
            *[{"issue_id": "retouch.journey." + identity, "title": identity,
               "source_message_ids": list(source_message_ids), "semantic_refs": [target],
               "acceptance_criteria": [identity], "verification_scope": "synthetic_retouch_mechanics_only"}
              for identity, target in RETOUCH_OBLIGATIONS.items()],
            *[{'issue_id': 'retouch.requirement.cost.' + identity, 'title': identity,
               'source_message_ids': list(source_message_ids), 'semantic_refs': ['view:d_photo_editor'],
               'acceptance_criteria': [identity], 'verification_scope': 'owned_cost_projection_only'} for identity in COST_CASES]],
            "acceptance_constraints": [*(change or {}).get("acceptance_constraints", []), *specs]},
        semantic_document=semantic_document, behavioral_context=context)
    if not source_message_ids:
        graph['gaps'].append({'kind': 'application_journey_canonical_source_missing'})
        receipt.update(ok=False, repairable=False)
        receipt['failures'].append({'failure': 'application_journey_canonical_source_missing'})
    # A registered business profile does not silently certify every inherited
    # project Issue. Preserve legacy gaps and disclose the exact coverage set.
    verified_requirements = {'retouch.journey.' + identity for identity in RETOUCH_OBLIGATIONS}
    verified_requirements.update('retouch.requirement.cost.' + identity for identity in COST_CASES)
    declared_requirements = {str(issue.get('issue_id')) for issue in (change or {}).get('issues', [])
                             if isinstance(issue, dict) and issue.get('issue_id')}
    receipt['verification_coverage'] = {
        'authority': 'registered_profile_only',
        'canonical_source_message_ids': list(source_message_ids),
        'verified_requirement_refs': sorted(verified_requirements) if receipt['ok'] else [],
        'unverified_project_requirement_refs': sorted(declared_requirements - verified_requirements),
        'project_requirements_complete': bool(receipt['ok'] and not (declared_requirements - verified_requirements)
                                              and not graph['gaps']),
        'retained_gaps': graph['gaps'],
    }
    receipt["traceability"] = graph
    if unchanged and all(row and row.get("checks") and row.get("client_assets") and row.get("final_audit")
        and not row.get("errors") and row.get("specification", {}).get("source_digest") == source_digest
        and row.get("observed_materializations") and all(all(observed.get(k) == v for k, v in expected_materialization.items())
            for observed in row["observed_materializations"]) for row in reports.values()):
        observations = {spec["constraint_id"]: {"passed": all(any(check.get("id") == spec["expected"]
            and check.get("passed") is True for check in row.get("checks", []))
            and (spec['expected'] != 'compare-zoom-pan-difference-download'
                 or row.get('independent_pixel_verification', {}).get('passed') is True) for row in reports.values()),
            "evidence_ref": "artifact:" + str(output.relative_to(repo)).replace("\\", "/")}
            for spec in specs if spec['constraint_id'] != 'retouch.execution_integrity'
            and not spec['constraint_id'].startswith('retouch.cost.')}
        observations.update({'retouch.cost.' + identity: {'passed': any(row.get('id') == identity and row.get('passed') is True
            for row in cost_report.get('cases', [])), 'evidence_ref': 'artifact:' + str((output / 'cost-authority/report.json').relative_to(repo)).replace('\\', '/')}
            for identity in COST_CASES})
        observations['retouch.execution_integrity'] = {'passed': receipt['ok'],
            'evidence_ref': 'artifact:' + str(output.relative_to(repo)).replace('\\', '/')}
        behavior = make_behavioral_receipt(graph["graph"], verification_refs=list(observations),
            execution_ref="application-journey:" + label, observations=observations)
        receipt["traceability"].update(receipt=behavior, graph=attach_behavioral_execution(graph["graph"], behavior))
    receipt_path = output / "receipt.json"
    atomic_write_json(receipt_path, receipt)
    receipt.update(receipt_path=str(receipt_path), receipt_digest=file_digest(receipt_path))
    return receipt
