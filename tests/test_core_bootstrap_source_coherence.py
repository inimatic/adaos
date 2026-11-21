from pathlib import Path

import pytest

from adaos.apps import core_update_apply
from adaos.services import core_update


@pytest.mark.parametrize('relative', ['src/adaos/services/new_admission.py',
    'src/adaos/abi/new_contract.json', 'src/adaos/skills_templates/new/template.yaml', 'uv.lock',
    'src/adaos/integrations/automation.sha'])
@pytest.mark.parametrize('removed', [False, True])
def test_bootstrap_compares_new_and_removed_runtime_inputs(tmp_path, relative, removed):
    root, candidate = tmp_path / 'root', tmp_path / 'candidate'
    root.mkdir()
    candidate.mkdir()
    target = (root if removed else candidate) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('changed runtime input', encoding='utf-8')
    result = core_update_apply._detect_bootstrap_promotion_requirement(candidate, root)
    assert result['required'] is True and relative in result['changed_paths']


def test_bootstrap_excludes_source_integrations_and_generated_caches(tmp_path):
    root, candidate = tmp_path / 'root', tmp_path / 'candidate'
    root.mkdir()
    candidate.mkdir()
    for relative in ('src/adaos/integrations/client/client.json', 'src/adaos/__pycache__/cache.pyc',
                     'docs-development/private.md', 'tests/test_core.py'):
        path = candidate / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('another delivery owner', encoding='utf-8')
    assert core_update_apply._detect_bootstrap_promotion_requirement(candidate, root)['required'] is False


@pytest.mark.parametrize('different', [True, False])
def test_policy_bound_slot_requires_equal_root_inputs_instead_of_git_ancestry(tmp_path, monkeypatch, different):
    root, candidate = tmp_path / 'root', tmp_path / 'candidate'
    relative = Path('src/adaos/abi/application.json')
    for base in (root, candidate):
        (base / relative).parent.mkdir(parents=True)
        (base / relative).write_text('same ABI', encoding='utf-8')
    if different:
        (root / relative).write_text('different root ABI', encoding='utf-8')
    monkeypatch.setattr(core_update, '_resolve_root_promotion_target', lambda _: (root, 'isolated_rehearsal'))
    monkeypatch.setattr(core_update, '_root_checkout_contains_candidate_commit',
        lambda *args: pytest.fail('Git ancestry must not authorize mixed policy-bound Core inputs'))
    manifest = {'repo_dir': str(candidate), 'git_commit': 'a' * 40,
        'source_policy': {'branch': 'main', 'revision': 2}, 'bootstrap_update': {'required': False}}
    required, result = core_update.resolved_root_promotion_requirement(manifest)
    assert required is different
    assert result['effective_root_commit_relation'] == 'policy_bound_exact_source'
    assert relative.as_posix() in result['effective_changed_paths']
