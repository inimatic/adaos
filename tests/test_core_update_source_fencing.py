from types import SimpleNamespace

import pytest

from adaos.services import core_slots, core_update, source_policy, node_runtime_state
from test_release_compatibility import composition


@pytest.fixture
def update(monkeypatch, tmp_path):
    # Runtime adapter imports may load the repository dotenv. Bind the test's
    # private node only after those imports, before any marker writes.
    from adaos.apps import supervisor
    monkeypatch.setenv('ADAOS_BASE_DIR', str(tmp_path))
    monkeypatch.setenv('ENV_TYPE', 'prod')
    monkeypatch.delenv('SRC_BRANCH', raising=False)
    conf = SimpleNamespace(role='hub', subnet_id='source-fencing-hub')
    monkeypatch.setattr(source_policy, '_config', lambda conf_=None: conf_ or conf)
    monkeypatch.setattr(node_runtime_state, '_state_path', lambda: tmp_path/'state/node_runtime.json')
    core_slots.activate_slot('A')
    plan = {'action':'update', 'target_rev':'main', 'target_version':'a'*40, 'target_slot':'B'}
    def prepare(**kwargs):
        return {'slot':kwargs['slot'], 'source_branch':kwargs['target_rev'],
                'git_commit':kwargs['target_version'], 'argv':['python', '-m', 'adaos']}
    monkeypatch.setattr('adaos.apps.core_update_apply.prepare_slot', prepare)
    monkeypatch.setattr(core_update, 'configured_update_command', lambda _: 'owned-test-command')
    calls = []
    def execute(_):
        calls.append('command')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(core_update, '_run_command_with_bounded_output', execute)
    return plan, calls


def test_prepared_source_retains_policy_and_activates_exact_identity(update):
    plan, calls = update
    prepared = core_update.prepare_pending_update(plan)
    assert prepared['state'] == 'prepared'
    assert prepared['manifest']['source_policy'] == prepared['plan']['source_policy']
    # The original durable intent can recover its policy from the prepared slot.
    result = core_update.execute_pending_update(plan)
    assert result['state'] == 'succeeded'
    assert calls == ['command'] and core_slots.active_slot() == 'B'
    assert node_runtime_state.load_node_runtime_state()['core_release_activation']['state'] == 'activated_unconfirmed'


@pytest.mark.parametrize('round_trip', [False, True])
def test_policy_change_after_prepare_prevents_command_and_retains_active(update, monkeypatch, round_trip):
    plan, calls = update
    assert core_update.prepare_pending_update(plan)['state'] == 'prepared'
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    source_policy.source_policy()
    if round_trip:
        monkeypatch.setenv('SRC_BRANCH', 'main')
        source_policy.source_policy()
    result = core_update.execute_pending_update(plan)
    assert result['state'] == 'failed' and result['phase'] == 'admission'
    assert not calls and core_slots.active_slot() == 'A'


def test_policy_change_during_prepare_does_not_claim_prepared(update, monkeypatch):
    plan, _ = update
    def prepare(**kwargs):
        monkeypatch.setenv('SRC_BRANCH', 'dev2027')
        return {'slot':'B', 'source_branch':'main', 'git_commit':'a'*40, 'argv':['python']}
    monkeypatch.setattr('adaos.apps.core_update_apply.prepare_slot', prepare)
    result = core_update.prepare_pending_update(plan)
    assert result['state'] == 'failed' and result['error'] == 'source_policy_changed'
    assert core_slots.active_slot() == 'A'


def test_member_uses_inherited_policy_instead_of_its_local_branch_environment(update):
    plan, calls = update
    source_policy._config().role = 'member'
    source_policy.inherit_source_policy({'schema':source_policy.SCHEMA, 'authority':'hub',
        'confirmed':True, 'hub_id':'source-fencing-hub', 'revision':4, 'branch':'dev2027'})
    plan['target_rev'] = 'dev2027'
    core_slots.write_slot_manifest('A', {'source_branch':'dev2027'})
    assert core_update.prepare_pending_update(plan)['state'] == 'prepared'
    assert core_update.execute_pending_update(plan)['state'] == 'succeeded'
    assert calls == ['command'] and core_slots.active_slot() == 'B'


@pytest.mark.parametrize('owner_commit', ['a'*40, 'b'*40])
def test_activation_is_not_confirmation_until_exact_serving_runtime_is_verified(update, monkeypatch, owner_commit):
    from adaos.services.core_update_admission import confirm_ready_source
    plan, _ = update
    core_update.prepare_pending_update(plan)
    core_update.execute_pending_update(plan)
    state = node_runtime_state.load_node_runtime_state()
    assert 'confirmed_core_release' not in state
    monkeypatch.setattr('adaos.build_info.runtime_source_identity', lambda: {
        'branch':'main', 'commit':owner_commit, 'version':'1.0.0'})
    confirmed = confirm_ready_source('B', core_slots.read_slot_manifest('B'))
    assert confirmed == (owner_commit == 'a'*40)
    state = node_runtime_state.load_node_runtime_state()
    if confirmed:
        assert state['confirmed_core_release']['state'] == 'confirmed'
        assert state['confirmed_core_release']['commit'] == owner_commit
    else:
        assert 'confirmed_core_release' not in state


def test_supervisor_cutover_uses_the_requested_commit_and_policy(update, monkeypatch):
    from adaos.apps import supervisor
    plan, _ = update
    prepared = core_update.prepare_pending_update(plan)
    core_update.write_status({'state':'restarting', 'phase':'shutdown', **plan})
    operations = supervisor.SupervisorManager._update_execution_operations()
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    with pytest.raises(source_policy.SourcePolicyError, match='source_policy_changed'):
        operations.activate_slot('B')
    assert core_slots.active_slot() == 'A'


@pytest.mark.parametrize('fault', ['policy', 'commit', 'branch', 'prepared_policy'])
def test_policy_or_slot_replacement_during_command_cannot_activate(update, monkeypatch, fault):
    plan, _ = update
    assert core_update.prepare_pending_update(plan)['state'] == 'prepared'
    def changed(_):
        if fault == 'policy':
            monkeypatch.setenv('SRC_BRANCH', 'dev2027')
        else:
            manifest = core_slots.read_slot_manifest('B')
            if fault == 'commit': manifest['git_commit'] = 'b'*40
            elif fault == 'branch': manifest['source_branch'] = 'dev2027'
            else: manifest['source_policy']['revision'] += 1
            core_slots.write_slot_manifest('B', manifest)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(core_update, '_run_command_with_bounded_output', changed)
    result = core_update.execute_pending_update(plan)
    assert result['state'] == 'failed' and result['prepared_retained'] is True
    assert core_slots.active_slot() == 'A'
    assert core_slots.read_slot_manifest('B') is not None


def test_branch_transition_without_composition_preserves_active_slot(update, monkeypatch):
    plan, calls = update
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    plan['target_rev'] = 'dev2027'
    result = core_update.prepare_pending_update(plan)
    assert result['state'] == 'failed' and result['error'] == 'branch_transition_compatibility_required'
    assert not calls and core_slots.active_slot() == 'A'


@pytest.mark.parametrize('change_data', [False, True])
def test_transition_rechecks_actual_data_generation_before_activation(update, composition, monkeypatch, change_data):
    plan, calls = update
    contract, observations, _ = composition
    contract['data']['node']['writes_schema'] = 'v2'
    observations['data']['node']['prepared_migration'] = {'from_schema':'v1','to_schema':'v2',
        'generation':7, 'status':'prepared', 'checkpoint_digest':'sha256:'+'c'*64,
        'restore_receipt_digest':'sha256:'+'d'*64}
    node_runtime_state.mutate_node_runtime_state(lambda state: state.update(release_composition=observations))
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    plan['target_rev'] = 'dev2027'
    monkeypatch.setattr('adaos.apps.core_update_apply.prepare_slot', lambda **kwargs: {
        'slot':'B', 'source_branch':'dev2027', 'git_commit':'a'*40, 'argv':['python'],
        'release_compatibility':contract})
    assert core_update.prepare_pending_update(plan)['state'] == 'prepared'
    def execute(_):
        if change_data:
            def advance(state): state['release_composition']['data']['node']['generation'] += 1
            node_runtime_state.mutate_node_runtime_state(advance)
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(core_update, '_run_command_with_bounded_output', execute)
    result = core_update.execute_pending_update(plan)
    assert result['state'] == ('failed' if change_data else 'succeeded')
    assert core_slots.active_slot() == ('A' if change_data else 'B')


def test_executable_rollback_does_not_restore_or_accept_incompatible_data(update, composition):
    from adaos.services.release_compatibility import ReleaseCompatibilityError
    contract, observations, _ = composition
    core_slots.write_slot_manifest('A', {'source_branch':'main', 'release_compatibility':contract})
    core_slots.activate_slot('B')
    observations['data']['node']['schema'] = 'v3'
    node_runtime_state.mutate_node_runtime_state(lambda state: state.update(release_composition=observations))
    with pytest.raises(ReleaseCompatibilityError, match='schema_incompatible'):
        core_slots.rollback_to_previous_slot()
    assert core_slots.active_slot() == 'B'
    result = core_update.execute_pending_update({'action':'rollback'})
    assert result['state'] == 'failed' and result['phase'] == 'rollback_admission'
    assert result['data_rollback_performed'] is False
    assert node_runtime_state.load_node_runtime_state()['release_composition']['data'] == observations['data']
    observations['data']['node']['schema'] = 'v1'
    node_runtime_state.mutate_node_runtime_state(lambda state: state.update(release_composition=observations))
    assert core_slots.rollback_to_previous_slot() == 'A'


@pytest.mark.parametrize('stopped_after_marker',[False,True])
def test_readiness_reconciles_only_the_exact_durable_activation_after_interruption(update,monkeypatch,stopped_after_marker):
    from adaos.services.core_update_admission import activate_prepared_source, confirm_ready_source
    plan,_ = update
    prepared = core_update.prepare_pending_update(plan)
    write = node_runtime_state._write_text_atomically
    def interrupted(path,text):
        raise OSError('interrupted after marker selection')
    def activate(slot):
        if not stopped_after_marker:
            raise OSError('interrupted before marker selection')
        core_slots.activate_slot(slot)
    monkeypatch.setattr(node_runtime_state,'_write_text_atomically',interrupted)
    with pytest.raises(OSError,match='interrupted'):
        activate_prepared_source(prepared['plan'],activate)
    assert 'core_release_activation' not in node_runtime_state.load_node_runtime_state()
    monkeypatch.setattr(node_runtime_state,'_write_text_atomically',write)
    monkeypatch.setattr('adaos.build_info.runtime_source_identity',lambda:{
        'branch':'main','commit':'a'*40,'version':'1.0.0'})
    assert confirm_ready_source('B',core_slots.read_slot_manifest('B')) is stopped_after_marker
    state = node_runtime_state.load_node_runtime_state()
    if stopped_after_marker:
        assert state['core_release_activation']['recovered_after_interruption'] is True
        assert state['confirmed_core_release']['slot'] == 'B'
    else:
        assert 'confirmed_core_release' not in state and core_slots.active_slot() == 'A'


def test_restart_reconciles_selected_target_before_running_installer_again(update,monkeypatch):
    plan,calls=update
    prepared=core_update.prepare_pending_update(plan)
    assert core_update.execute_pending_update(plan)['state']=='succeeded'
    # Model an outgoing process still bound to A after next-boot B was selected.
    monkeypatch.setenv('ADAOS_ACTIVE_CORE_SLOT','A')
    assert core_slots.active_slot()=='A' and core_slots.selected_slot()=='B'
    result=core_update.execute_pending_update(prepared['plan'])
    assert result['state']=='restarting' and result['phase']=='readiness'
    assert result['activation_reconciled'] is True and calls==['command']
    assert 'confirmed_core_release' not in node_runtime_state.load_node_runtime_state()


def test_stale_intent_never_reinstalls_over_the_selected_slot(update):
    plan,calls=update
    core_update.prepare_pending_update(plan)
    core_update.execute_pending_update(plan)
    changed={**plan,'target_version':'b'*40}
    result=core_update.execute_pending_update(changed)
    assert result['state']=='failed' and result['phase']=='admission'
    assert result['error']=='selected_slot_belongs_to_another_activation'
    assert calls==['command'] and core_slots.selected_slot()=='B'
