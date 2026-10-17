import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.builder.application_journeys import RETOUCH_OBLIGATIONS, RETOUCH_CASES, COST_CASES, qualify_reports, qualify_cost_report
from adaos.services.builder.application_journeys import evaluate_retouch, file_digest
from adaos.services.builder.automation import BuilderAutomationService


def sample():
    return {"passed": True, "checks": [{"id": key, "passed": True} for key in RETOUCH_OBLIGATIONS],
        "specification": {"source_digest": "source", "obligations": list(RETOUCH_OBLIGATIONS)},
        "observed_materializations": [{"revision": "task", "source_fingerprint": "fingerprint"}],
        "client_assets": [{"path": "/main.js", "digest": "sha256:asset", "size_bytes": 100}],
        "browser_environment": {"browser_version": "verified-browser", "viewport": {"width": 1440, "height": 1000}},
        "final_audit": {"external_model_calls": 0, "source_digest": "source",
                        "environment": {"python": "3.11", "platform": "test", "pillow": "12.3", "numpy": "test"}},
        "independent_pixel_verification": {"passed": True}}


def qualify(reports):
    expanded = {}
    for case in RETOUCH_CASES:
        variant, layout = case.split('/')
        if layout in reports:
            expanded[case] = copy.deepcopy(reports[layout])
            expanded[case]['specification'].update(variant=variant, layout=layout)
    return qualify_reports(expanded, expected_materialization={"revision": "task", "source_fingerprint": "fingerprint"},
                           source_digest="source")


@pytest.mark.parametrize("mutation", ["missing_check", "failed_check", "duplicate_check", "missing_layout",
    "missing_materialization", "stale_materialization", "source_drift", "missing_asset", "unhashed_asset",
    "missing_audit", "fixture_source_drift", "provider_call", "supervisor", "pixel_failure", "report_red",
    "runner_error", "missing_spec_obligation", "missing_browser_environment", "missing_fixture_environment"])
def test_green_runner_cannot_hide_missing_obligations_or_execution_dependencies(mutation):
    reports = {"wide": sample(), "compact": sample()}
    row = reports["wide"]
    if mutation == "missing_check": row["checks"].pop()
    if mutation == "failed_check": row["checks"][0]["passed"] = False
    if mutation == "duplicate_check": row["checks"].append(copy.deepcopy(row["checks"][0]))
    if mutation == "missing_layout": reports.pop("compact")
    if mutation == "missing_materialization": row.pop("observed_materializations")
    if mutation == "stale_materialization": row["observed_materializations"][0]["revision"] = "previous-task"
    if mutation == "source_drift": row["specification"]["source_digest"] = "changed"
    if mutation == "missing_asset": row["client_assets"] = []
    if mutation == "unhashed_asset": row["client_assets"][0].pop("digest")
    if mutation == "missing_audit": row.pop("final_audit")
    if mutation == "fixture_source_drift": row["final_audit"]["source_digest"] = "other"
    if mutation == "provider_call": row["final_audit"]["external_model_calls"] = 1
    if mutation == "supervisor": row["supervisor_interventions"] = ["manually closed modal"]
    if mutation == "pixel_failure": row["independent_pixel_verification"]["passed"] = False
    if mutation == "report_red": row["passed"] = False
    if mutation == "runner_error": row["errors"] = ["unexpected fixture outage"]
    if mutation == "missing_spec_obligation": row["specification"]["obligations"].pop()
    if mutation == "missing_browser_environment": row.pop('browser_environment')
    if mutation == "missing_fixture_environment": row['final_audit'].pop('environment')
    result = qualify(reports)
    assert not result["ok"] and not result["repairable"]


def test_complete_observations_pass_without_supervisor_intervention():
    result = qualify({"wide": sample(), "compact": sample()})
    assert result == {"ok": True, "failures": [], "repairable": False}


def test_only_observed_candidate_assertion_enters_existing_bounded_repair():
    reports = {"wide": sample(), "compact": sample()}
    reports["wide"].update(passed=False, active="analysis-one-submit-editable-plan",
        failure="expect(locator).not.toBeVisible: parameter modal hides progress",
        failure_kind="candidate_postcondition")
    result = qualify(reports)
    assert not result["ok"] and result["repairable"]
    assert BuilderAutomationService._browser_feedback_is_repairable({"application_journey": result})
    reports["compact"]["errors"] = ["fixture unavailable"]
    result = qualify(reports)
    assert not result["repairable"]
    assert not BuilderAutomationService._browser_feedback_is_repairable({"application_journey": result})


def test_empty_materialization_expectation_is_not_acceptance():
    result = qualify_reports({"wide": sample(), "compact": sample()}, expected_materialization={}, source_digest="source")
    assert not result["ok"]


def cost_sample(source='source'):
    return {'completed': True, 'source_digest': source, 'external_model_calls': 0, 'passed': True,
        'environment': {'python': '3.11', 'platform': 'test', 'pillow': '12.3', 'numpy': 'test'}, 'run_id': 'run',
        'attempts': [{'request_id': str(i), 'run_id': 'run', 'status': 'reconciled', 'cost_basis': 'metered', 'observed_usd': .01} for i in range(3)],
        'cases': [{'id': name, 'passed': True, 'observed_usd': .03, 'observed_known_usd': .03,
                   'text': 'confirmed spend: unknown; confirmed part: 0.03 USD' if name == COST_CASES[-1] else 'confirmed spend: 0.03 USD'} for name in COST_CASES]}


@pytest.mark.parametrize('fault', [None, 'source_changed', 'verifier_source_changed', 'nonzero_exit', 'postcondition', 'missing_source', 'legacy_gap', 'cost_postcondition'])
def test_registered_profile_preregisters_checks_and_retains_bound_behavioral_receipt(tmp_path, monkeypatch, fault):
    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / 'natural_retouch_skill'
    (source / 'handlers').mkdir(parents=True)
    (source / 'handlers/retouch.py').write_text('# immutable candidate fixture', encoding='utf-8')
    source_digest = 'sha256:' + hashlib.sha256(json.dumps({'retouch.py': file_digest(source / 'handlers/retouch.py')}, sort_keys=True).encode()).hexdigest()
    materialization = {'revision': 'task', 'source_fingerprint': 'fingerprint'}
    verification_ran = False
    if fault == 'verifier_source_changed':
        from adaos.services.builder import application_journeys
        monkeypatch.setattr(application_journeys, 'file_digest', lambda path:
            'sha256:changed-verifier' if verification_ran and Path(path).name == 'retouch-journey.mjs' else file_digest(path))
    def runner(command, **kwargs):
        nonlocal verification_ran
        output = Path(command[command.index('--output') + 1])
        declared = json.loads((output / 'verification-spec.json').read_text(encoding='utf-8'))
        assert len(declared['specifications']) == len(RETOUCH_OBLIGATIONS) + len(COST_CASES) + 1
        assert json.loads(kwargs['env']['ADAOS_E2E_EXPECTED_MATERIALIZATION']) == materialization
        for layout in RETOUCH_CASES:
            row = sample(); row['specification']['source_digest'] = source_digest
            variant, viewport = layout.split('/')
            row['specification'].update(variant=variant, layout=viewport)
            row['final_audit']['source_digest'] = source_digest
            if fault == 'postcondition':
                row.update(passed=False, active='budget-admission-failure-visible',
                           failure='expect(response.ok).toBe(false)', failure_kind='candidate_postcondition')
                row['checks'] = row['checks'][:5]
            (output / layout).mkdir(parents=True)
            (output / layout / 'journey.json').write_text(json.dumps(row), encoding='utf-8')
        cost = cost_sample(source_digest)
        if fault == 'cost_postcondition':
            cost['cases'][0].update(passed=False, observed_usd=None, text='confirmed spend: unknown')
            cost['passed'] = False
        (output / 'cost-authority').mkdir()
        (output / 'cost-authority/report.json').write_text(json.dumps(cost), encoding='utf8')
        verification_ran = True
        return SimpleNamespace(returncode=1 if fault in {'nonzero_exit', 'postcondition', 'cost_postcondition'} else 0, stdout='qualified', stderr='')
    result = evaluate_retouch(repo_root=repo, scenario_id='natural_retouch', task_id='task',
        implementation_paths=[source], environment={}, implementation_snapshot={'candidate': 'before'},
        expected_materialization=materialization,
        source_message_ids=[] if fault == 'missing_source' else ['m.canonical.user-request'],
        change={'issues': [{'issue_id': 'legacy.unmapped', 'title': 'unverified historical requirement'}]} if fault == 'legacy_gap' else None,
        semantic_document={'views': [{'id': ref.split(':', 1)[1]} for ref in set(RETOUCH_OBLIGATIONS.values()) if ref.startswith('view:')],
                           'commands': [{'id': 'cmd_add_photo'}]},
        snapshot_reader=lambda: {'candidate': 'after' if fault == 'source_changed' else 'before'}, command_runner=runner)
    assert result['ok'] is (fault in {None, 'legacy_gap'})
    assert result['repairable'] is (fault in {'postcondition', 'cost_postcondition'})
    assert Path(result['receipt_path']).is_file()
    nodes = result['traceability']['graph']['nodes']
    assert any(row['kind'] == 'source_message' and row['ref'] == 'm.canonical.user-request' for row in nodes) is (fault != 'missing_source')
    coverage = result['verification_coverage']
    assert len(coverage['verified_requirement_refs']) == (len(RETOUCH_OBLIGATIONS) + len(COST_CASES) if fault in {None, 'legacy_gap'} else 0)
    assert coverage['project_requirements_complete'] is (fault is None)
    assert bool(result['traceability']['gaps']) is (fault in {'legacy_gap', 'missing_source'})
    if fault == 'legacy_gap':
        assert coverage['unverified_project_requirement_refs'] == ['legacy.unmapped']
    assert any(row['relation'] == 'verified_by' and row['metadata'].get('authority') == 'explicit_constraint_requirement_refs'
               for row in result['traceability']['graph']['edges'])
    if fault not in {'source_changed', 'verifier_source_changed'}:
        behavior = result['traceability']['receipt']
        assert set(behavior['dependencies']) >= {'behavior:implementation', 'behavior:runtime', 'behavior:state', 'behavior:environment'}
        assert behavior['outcome'] == ('failed' if fault in {'postcondition', 'nonzero_exit', 'missing_source', 'cost_postcondition'} else 'passed')
    else:
        assert 'receipt' not in result['traceability']
        reason = 'trusted_verifier_changed_during_application_journey' if fault == 'verifier_source_changed' else 'candidate_changed_during_application_journey'
        assert any(row['failure'] == reason for row in result['failures'])
        other = 'candidate_changed_during_application_journey' if fault == 'verifier_source_changed' else 'trusted_verifier_changed_during_application_journey'
        assert not any(row['failure'] == other for row in result['failures'])


@pytest.mark.parametrize('fault', ['missing_case', 'missing_source', 'missing_environment', 'missing_metering', 'wrong_metering_basis', 'green_flag_lies', 'cross_run_meter', 'known_wrong_cost'])
def test_cost_verifier_cannot_accept_green_flags_without_authority_or_complete_cases(fault):
    report = cost_sample()
    if fault == 'missing_case': report['cases'].pop()
    if fault == 'missing_source': report['source_digest'] = 'different'
    if fault == 'missing_environment': report.pop('environment')
    if fault == 'missing_metering': report['attempts'].pop()
    if fault == 'wrong_metering_basis': report['attempts'][0]['cost_basis'] = 'estimate'
    if fault == 'green_flag_lies': report['cases'][0]['observed_usd'] = None
    if fault == 'cross_run_meter': report['attempts'][0]['run_id'] = 'other'
    if fault == 'known_wrong_cost': report['cases'][0]['observed_usd'] = .73
    result = qualify_cost_report(report, source_digest='source')
    assert not result['ok'] and not result['repairable']


