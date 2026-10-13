from pathlib import Path

import pytest

from adaos.e2e.semantic_proposer_comparison import changes, expected_after, fixtures, installed_runtime
from adaos.services.builder.semantic_prototype import compile_semantic_prototype


def test_frozen_change_chain_preserves_records_relationships_commands_and_other_locales():
    repo = Path(__file__).resolve().parents[1]
    for _name, source in fixtures(repo):
        document = compile_semantic_prototype(source, project_ref='project:test')['semantic_document']
        before = document
        for change in changes():
            document = expected_after(document, change)
            compile_semantic_prototype(document, project_ref='project:test')
        assert document['resources'][0]['records'] == before['resources'][0]['records']
        assert document['commands'] == before['commands']
        assert document['relationships'] == before['relationships']
        assert document['views'][0]['title']['ru'] == before['views'][0]['title']['ru']
        assert document['views'][1:] == before['views'][1:]


def test_runtime_pin_comes_from_live_installed_owner_and_rejects_source_drift(tmp_path):
    from types import SimpleNamespace
    manifest = tmp_path / 'runtime/skill.yaml'
    handler = manifest.parent / 'src/skills/builder_skill/handlers/main.py'
    source = tmp_path / '.adaos/workspace/skills/builder_skill/handlers/main.py'
    for path in (manifest, handler, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('same', encoding='utf-8')
    state = {'ready': True, 'active_slot': 'B', 'version': 'future', 'resolved_manifest': str(manifest)}
    seen = []
    def get(url, **kwargs):
        seen.append(url)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'state': state})
    client = SimpleNamespace(get=get)
    assert installed_runtime(client, tmp_path)[0]['version'] == 'future'
    assert seen[0].endswith('/api/skills/runtime/status/builder_skill')
    source.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='differs from Workspace'):
        installed_runtime(client, tmp_path)
    state['ready'] = False
    with pytest.raises(ValueError, match='must be ready'):
        installed_runtime(client, tmp_path)


def test_whole_series_budget_counts_prior_batches_and_unknown_attempts():
    from adaos.e2e.semantic_proposer_comparison import check_series_budget
    budget = dict(prior_paid_requests=8, reserved_attempts=23, prior_cost_usd=0.3,
                  known_cost_usd=0.7, unknown_reserved_usd=0, max_usd=15)
    check_series_budget(**budget)  # one final request is admitted
    with pytest.raises(ValueError, match='budget exhausted'):
        check_series_budget(**{**budget, 'reserved_attempts': 24})
    with pytest.raises(ValueError, match='budget exhausted'):
        check_series_budget(**{**budget, 'unknown_reserved_usd': 14})
