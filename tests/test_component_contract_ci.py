"""Keep the declared contract rails mandatory in the existing CI job."""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_component_contract_tests_and_generators_are_required_ci_gates():
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
    steps = workflow['jobs']['builder_contracts']['steps']
    required = [step.get('run', '') for step in steps if not step.get('continue-on-error') and not step.get('if')]
    commands = '\n'.join(required)
    for module in ('ui_component_contracts', 'contract_context_admission', 'component_contract_reference',
                   'component_matched_evolution', 'component_contract_mcp', 'builder_contract_usage', 'component_contract_ci', 'component_contract_transitions'):
        assert f'tests/test_{module}.py' in commands
    assert 'scripts/generate_ui_component_contract.py --check' in commands
    consumers = {step.get('with', {}).get('repository'): step['with']
                 for step in steps if step.get('uses') == 'actions/checkout@v4' and 'repository' in step.get('with', {})}
    for name, secret in (('client', 'ADAOS_CLIENT_READONLY_DEPLOY_KEY'), ('backend', 'ADAOS_BACKEND_READONLY_DEPLOY_KEY')):
        checkout = consumers[f'inimatic/adaos-{name}']
        assert checkout['ref'] == '${{ steps.consumer-pins.outputs.' + name + ' }}'
        assert checkout['ssh-key'] == '${{ secrets.' + secret + ' }}'
        assert checkout['persist-credentials'] is False
        assert 'token' not in checkout
    job = workflow['jobs']['builder_contracts']
    assert job['environment'] == 'private-contract-consumers'
    assert job['if'] == "github.event_name != 'pull_request'"
    assert 'INFRA_DISPATCH_TOKEN' not in str(job)
    assert 'scripts/run_private_contract_check.py' in commands
    assert '--public-junit reports/builder-contracts.xml' in commands
    assert 'npm run test:boundaries' in commands and 'tsc --noEmit' in commands
    assert 'component-contract-write-proof.test.mjs' in commands
