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


def test_ordinary_packet_produces_and_pins_query_without_artifact_injection(tmp_path):
    import json
    import subprocess
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker
    from adaos.services.capability_binding_state import PortableContractCatalog
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    repo = tmp_path/'repo'
    registry = repo/'.adaos/workspace'
    _write_registry(registry, (capability, binding, delivery, evidence))
    for args in [['init'], ['add', '.'], ['-c', 'user.name=E2E', '-c', 'user.email=e2e@example.invalid', 'commit', '-m', 'Registry']]:
        subprocess.run(['git', '-C', str(registry), *args], check=True, capture_output=True)
    state = tmp_path/'state'
    catalog = PortableContractCatalog(state/'capability-binding-state/portable')
    for record in (capability, binding, delivery, evidence):
        catalog.put(record)
    workspace = tmp_path/'candidate'
    scenario = workspace/'scenarios/mail_consumer'
    scenario.mkdir(parents=True)
    webui = {'schema': 'adaos.webui.v1', 'ui': {'application': {'desktop': {'pageSchema': {
        'widgets': [], 'meta': {'builder': {'cbs_intent': {'schema': 'adaos.builder.cbs_intent.v1',
            'requirements': [{'id': 'mail', 'capability_ref': capability.capability_ref, 'contract_range': '^1.0.0',
                'origin': 'human_explicit', 'required_authorities': ['providers.google.gmail']}]}}}}}}}}
    (scenario/'webui.json').write_text(json.dumps(webui), encoding='utf-8')
    assignment = {'task_id': 'task.ordinary-discovery', 'target': {'type': 'scenario', 'id': 'mail_consumer'},
        'realize_request': {'artifacts': {'implementation_brief': 'Implement mail application',
            # This caller-controlled authority must not reach owned discovery.
            'semantic_discovery_query': _query(_write_registry(registry, (capability, binding, delivery, evidence)), profile, requirement)}}}
    worker = LocalSkillFactoryWorker(state_dir=state, repo_root=repo, dev_skills_root=tmp_path/'skills',
        dev_scenarios_root=tmp_path/'scenarios')
    input_dir = tmp_path/'input'
    worker._build_packet(assignment, workspace, input_dir)
    query = assignment['realize_request']['artifacts']['semantic_discovery_query']
    assert query['environment_profile']['authorities'] == []
    assert query['requirements'][0]['capability_ref'] == capability.capability_ref
    assert query == json.loads((input_dir/'semantic-discovery-query.json').read_text())
    bindings = json.loads((input_dir/'implementation-bindings.json').read_text())
    reuse = bindings['portable_contract_reuse']
    assert reuse['requirements'][0]['reusable_bindings'] == []
    assert reuse['semantic_discovery']['registry_result']['requirements'][0]['rejections']
    assert reuse['semantic_discovery']['callable_tools'] == []
    worker._build_packet(assignment, workspace, input_dir)
    assert assignment['realize_request']['artifacts']['semantic_discovery_query'] == query


def test_factory_assignment_produces_discovery_from_its_sealed_source(tmp_path, monkeypatch):
    import json
    import subprocess
    from adaos.services.skill_factory import SkillFactoryService
    from adaos.services.skill_factory_sources import capture_source_snapshot
    from adaos.services import runtime_paths
    repo = tmp_path/'repo'
    registry = repo/'.adaos/workspace'
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    _write_registry(registry, (capability, binding, delivery, evidence))
    for args in [['init'], ['add', '.'], ['-c', 'user.name=E2E', '-c', 'user.email=e2e@example.invalid', 'commit', '-m', 'Registry']]:
        subprocess.run(['git', '-C', str(registry), *args], check=True, capture_output=True)
    source = tmp_path/'scenario'
    source.mkdir()
    webui = {'ui': {'application': {'desktop': {'pageSchema': {'meta': {'builder': {'cbs_intent': {
        'schema': 'adaos.builder.cbs_intent.v1', 'requirements': [{'id': 'mail', 'capability_ref': capability.capability_ref,
            'contract_range': '^1.0.0', 'origin': 'human_explicit'}]}}}}}}}}
    (source/'webui.json').write_text(json.dumps(webui))
    state = tmp_path/'state'
    snapshot = capture_source_snapshot(state_dir=state, artifacts=[('scenario', 'mail_consumer', source)], created_at='2026-10-14T00:00:00Z')
    monkeypatch.setattr(runtime_paths, 'current_control_repo_root', lambda: repo)
    factory = SkillFactoryService(state_dir=state)
    factory.submit_realize_request({'target': {'type': 'scenario', 'id': 'mail_consumer'},
        'repo': {'base_revision': snapshot['digest'], 'source_snapshot': snapshot, 'sparse_paths': ['scenarios/mail_consumer/']}})
    factory.register_dev_node({'node_id': 'devnode.discovery'})
    assignment = factory.poll_assignment('devnode.discovery')['assignment']
    artifacts = assignment['realize_request']['artifacts']
    assert artifacts['semantic_discovery_input']['status'] == 'prepared'
    query = artifacts['semantic_discovery_query']
    assert query['requirements'][0]['capability_ref'] == capability.capability_ref
    assert query['environment_profile']['authorities'] == []
    assert query['snapshot']['registry_revision'] == subprocess.check_output(['git', '-C', str(registry), 'rev-parse', 'HEAD'], text=True).strip()


def test_ordinary_reuse_selects_compatible_contract_before_version_rank(tmp_path):
    import json
    import subprocess
    from datetime import datetime, timezone
    from adaos.domain.capability_binding_state import EnvironmentProfile
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker
    from adaos.services.capability_binding_state import PortableContractCatalog
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    # This independent provider needs no external authority or dependency.
    profile = EnvironmentProfile.create(profile_ref='profile:local/default', profile_class='local', modes=('production',),
        provider_features=(), authorities=(), guarantees={'consistency': [], 'durability': [], 'isolation': []})
    capability = type(capability)._create({**capability.to_dict(), 'authority_requirements': []})
    binding = type(binding)._create({**binding.to_dict(), 'authority_requirements': [],
        'environment_constraints': {'profile_classes': ['local'], 'provider_features': []}})
    delivery = type(delivery)._create({**delivery.to_dict(), 'binding_definition_digest': binding.digest})
    evidence = type(evidence)._create({**evidence.to_dict(), 'issued_at': datetime.now(timezone.utc).isoformat(), 'dependencies': [],
        'environment': {'profile_ref': profile.profile_ref, 'profile_digest': profile.digest}, 'subjects': [
            {'kind': 'capability_contract', 'ref': capability.capability_ref, 'digest': capability.digest},
            {'kind': 'binding_definition', 'ref': binding.binding_definition_ref, 'digest': binding.digest}]})
    newer = type(capability)._create({**capability.to_dict(), 'version': '1.1.0', 'authority_requirements': ['unobserved.external.authority']})
    repo = tmp_path/'repo'
    registry = repo/'.adaos/workspace'
    records = (capability, newer, binding, delivery, evidence)
    _write_registry(registry, records)
    for args in [['init'], ['add', '.'], ['-c', 'user.name=E2E', '-c', 'user.email=e2e@example.invalid', 'commit', '-m', 'Registry']]:
        subprocess.run(['git', '-C', str(registry), *args], check=True, capture_output=True)
    state = tmp_path/'state'
    catalog = PortableContractCatalog(state/'capability-binding-state/portable')
    for record in records: catalog.put(record)
    workspace = tmp_path/'candidate'
    scenario = workspace/'scenarios/mail_consumer'
    scenario.mkdir(parents=True)
    webui = {'schema': 'adaos.webui.v1', 'ui': {'application': {'desktop': {'pageSchema': {'widgets': [], 'meta': {
        'builder': {'cbs_intent': {'schema': 'adaos.builder.cbs_intent.v1', 'requirements': [
            {'id': 'mail', 'capability_ref': capability.capability_ref, 'contract_range': '^1.0.0', 'origin': 'human_explicit'}]}}}}}}}}
    (scenario/'webui.json').write_text(json.dumps(webui))
    worker = LocalSkillFactoryWorker(state_dir=state, repo_root=repo, dev_skills_root=tmp_path/'skills', dev_scenarios_root=tmp_path/'scenarios')
    assignment = {'task_id': 'task.positive-reuse', 'target': {'type': 'scenario', 'id': 'mail_consumer'},
        'realize_request': {'artifacts': {'implementation_brief': 'Implement mail application'}}}
    worker._build_packet(assignment, workspace, tmp_path/'input')
    reuse = json.loads((tmp_path/'input/implementation-bindings.json').read_text())['portable_contract_reuse']
    assert reuse['requirements'][0]['selected_digest'] == capability.digest
    assert reuse['requirements'][0]['reusable_bindings'][0]['binding']['digest'] == binding.digest
    assert reuse['semantic_discovery']['candidates'][0]['binding_availability'] == 'unavailable'
    assert reuse['semantic_discovery']['callable_tools'] == []
