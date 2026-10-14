import copy
from test_semantic_registry_query import _fixture_records, _write_registry, _query, REVISION
from adaos.services.builder.authoring_discovery import qualified_semantic_discovery, component_authoring_discovery
from adaos.services.ui_component_contracts import project_component_contract
from adaos.e2e.component_contract_conformance import load_slice
from pathlib import Path


def test_authoring_compatibility_local_binding_and_task_tools_are_distinct(tmp_path):
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    root = tmp_path / 'registry'
    index = _write_registry(root, (capability, binding, delivery, evidence))
    query = _query(index, profile, requirement)
    def discover(**kwargs):
        return qualified_semantic_discovery(query, registry_root=root, index=index, registry_revision=REVISION, **kwargs)
    portable = discover()
    assert portable['candidates'][0]['semantic_support'] == 'supported'
    assert portable['candidates'][0]['binding_availability'] == 'unavailable'
    assert not portable['callable_tools']
    local = discover(local_admit=lambda candidate: {'status': 'admitted', 'callable_tools': ['read', 'write']}, task_tools=['read'])
    assert local['callable_tools'] == ['read'] and not local['activation_performed']
    denied = discover(local_admit=lambda candidate: {'status': 'denied', 'findings': [{'code': 'authority.denied'}]}, task_tools=['read'])
    assert denied['candidates'][0]['findings'] == [{'code': 'authority.denied'}] and not denied['callable_tools']
    query['evidence_inputs']['as_of'] = '2026-10-14T00:00:00+00:00'
    stale = discover()
    assert stale['candidates'] == [] and stale['registry_result']['requirements'][0]['rejections']


def test_normal_component_authoring_uses_exact_locators_and_task_dependency_closure():
    document, _ = load_slice(Path(__file__).resolve().parents[1])
    selected = component_authoring_discovery(document, [{'ref': 'view:records-list', 'properties': ['scope_filters.team_id.value']}],
        project_component_contract(), editable=True)
    assert selected['required_closure'] == ['ui.queryToolbar', 'ui.table']
    assert selected['binding_availability'] == {'authoring': 'available', 'production': 'not_assessed'}
    assert not selected['callable_tools'] and all(row['locator']['expected_source_digest'] for row in selected['candidates'])


def test_normal_worker_filters_rejected_bindings_and_uses_actual_registry_revision(tmp_path):
    import subprocess
    import pytest
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker
    from adaos.services.capability_binding_state import PortableContractCatalog
    from adaos.services.capability_binding_state.registry_query import SemanticRegistryQueryError
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    registry = tmp_path / 'repo/.adaos/workspace'
    index = _write_registry(registry, (capability, binding, delivery, evidence))
    for args in [['init'], ['add', '.'], ['-c', 'user.name=E2E', '-c', 'user.email=e2e@example.invalid', 'commit', '-m', 'Frozen registry']]:
        subprocess.run(['git', '-C', str(registry), *args], check=True, capture_output=True)
    revision = subprocess.check_output(['git', '-C', str(registry), 'rev-parse', 'HEAD'], text=True).strip()
    state = tmp_path / 'state'
    catalog = PortableContractCatalog(state/'capability-binding-state/portable')
    for record in (capability, binding, delivery, evidence):
        catalog.put(record)
    worker = LocalSkillFactoryWorker(state_dir=state, repo_root=tmp_path/'repo',
        dev_skills_root=tmp_path/'skills', dev_scenarios_root=tmp_path/'scenarios')
    webui = {'ui': {'application': {'desktop': {'pageSchema': {'meta': {'builder': {'cbs_intent': {
        'schema': 'adaos.builder.cbs_intent.v1', 'requirements': [{'id': 'mail', 'capability_ref': capability.capability_ref,
            'contract_range': '^1.0.0', 'origin': 'human_explicit'}]}}}}}}}}
    query = _query(index, profile, requirement)
    query['snapshot']['registry_revision'] = revision
    result = worker._portable_contract_reuse_bundle(webui, discovery_request=query)
    assert result['requirements'][0]['reusable_bindings']
    assert not result['semantic_discovery']['callable_tools']
    query['evidence_inputs']['as_of'] = '2026-10-14T00:00:00+00:00'
    rejected = worker._portable_contract_reuse_bundle(webui, discovery_request=query)
    assert rejected['requirements'][0]['reusable_bindings'] == []
    query['snapshot']['registry_revision'] = '0' * 40
    with pytest.raises(SemanticRegistryQueryError) as error:
        worker._portable_contract_reuse_bundle(webui, discovery_request=query)
    assert error.value.code == 'snapshot_drift'
