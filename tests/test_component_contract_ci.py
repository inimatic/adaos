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
                   'component_matched_evolution', 'component_contract_mcp', 'builder_contract_usage', 'component_contract_ci'):
        assert f'tests/test_{module}.py' in commands
    assert 'scripts/generate_ui_component_contract.py --check' in commands
    assert 'git submodule update --init --depth 1 src/adaos/integrations/adaos-client' in commands
    assert 'npm run test:boundaries' in commands and 'tsc --noEmit' in commands
    assert 'component-contract-write-proof.test.mjs' in commands
