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
def test_delayed_supervisor_request_expires_before_any_prepare_or_stop(update, monkeypatch, round_trip):
    from adaos.apps import supervisor
    from adaos.services.core_update_admission import bind_transition_source
    plan, calls = update
    bind_transition_source(plan)
    request = supervisor._request_from_attempt({**plan, 'state': 'planned'})
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    source_policy.source_policy()
    if round_trip:
        monkeypatch.setenv('SRC_BRANCH', 'main')
        source_policy.source_policy()
    manager = supervisor.SupervisorManager(runtime_host='127.0.0.1', runtime_port=8777, token='test')
    def never_prepare(**kwargs):
        pytest.fail('stale queued request must not acquire the migration lease')
    monkeypatch.setattr(manager, '_acquire_skill_runtime_migration_gate', never_prepare)
    result = manager._begin_prepare_transition(request)
    assert result['accepted'] is False and result['reason'] == 'source_policy_changed'
    assert result['status']['phase'] == 'source_admission'
    assert result['status']['state'] == 'cancelled'
    assert not calls and core_slots.active_slot() == 'A'


def test_legacy_delayed_production_request_cannot_recapture_current_policy(update):
    from adaos.services.core_update_admission import bind_transition_source
    plan, _ = update
    with pytest.raises(source_policy.SourcePolicyError, match='queued_source_policy_required'):
        bind_transition_source(plan, resumed=True)
    assert 'source_policy' not in plan


def test_transition_deduplication_includes_original_policy_and_branch(update):
    from adaos.apps import supervisor
    from adaos.services.core_update_admission import bind_transition_source
    plan, _ = update
    bind_transition_source(plan)
    restored = supervisor._request_from_attempt(plan)
    assert restored['source_policy'] == plan['source_policy']
    assert supervisor._transition_request_same_target(restored, plan)
    changed = {**plan, 'source_policy': {**plan['source_policy'], 'revision': plan['source_policy']['revision'] + 1}}
    assert not supervisor._transition_request_same_target(restored, changed)
    core_slots.write_slot_manifest('A', {**plan, 'git_commit': plan['target_version'], 'source_branch': 'dev2027'})
    assert not supervisor._transition_request_matches_active_slot(restored)


def test_supervisor_rejects_stale_incoming_policy_before_queueing(update, monkeypatch):
    import asyncio
    from adaos.apps import supervisor
    plan, _ = update
    policy = source_policy.source_policy()
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    source_policy.source_policy()
    manager = supervisor.SupervisorManager(runtime_host='127.0.0.1', runtime_port=8777, token='test')
    monkeypatch.setattr(supervisor, 'core_update_reactions_disabled_reason', lambda: None)
    def never_read_runtime():
        pytest.fail('stale incoming request must be rejected before runtime/queue mutation')
    monkeypatch.setattr(manager, 'status', never_read_runtime)
    result = asyncio.run(manager.start_update(action='update', target_rev=plan['target_rev'],
        target_version=plan['target_version'], source_policy_snapshot=policy, reason='root.release',
        countdown_sec=0, drain_timeout_sec=1, signal_delay_sec=0))
    assert result['accepted'] is False and result['reason'] == 'source_policy_changed'


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


@pytest.mark.parametrize('fault', [None, 'wrong_commit', 'wrong_instance', 'wrong_slot',
    'unverified', 'expired', 'candidate', 'write_fenced', 'not_ready', 'dirty_source', 'wrong_package'])
def test_confirmation_uses_the_verified_api_process_instead_of_supervisor_identity(update, monkeypatch, fault):
    import time
    from adaos.services.core_update_admission import confirm_ready_source
    from adaos.services import runtime_identity
    plan, _ = update
    core_update.prepare_pending_update(plan)
    core_update.execute_pending_update(plan)
    # The Supervisor's Python process has another version/instance. It cannot
    # establish which code is serving the user-facing API.
    monkeypatch.setattr('adaos.build_info.runtime_source_identity', lambda: {
        'branch': 'main', 'commit': 'c' * 40, 'version': '0.9.0'})
    monkeypatch.setattr(runtime_identity, 'runtime_instance_id', lambda: 'rt-supervisor')
    manifest = core_slots.read_slot_manifest('B')
    manifest.update(source_kind='source_archive', source_archive_sha256='d' * 64,
        source_checkout={'attempts': [{'source': 'source_archive', 'state': 'succeeded', 'archive_sha256': 'd' * 64}]})
    core_slots.write_slot_manifest('B', manifest)
    actual = {'branch': 'main', 'commit': 'a' * 40, 'version': '1.0.0', 'source_dirty': False,
        'package_digest': 'sha256:' + 'd' * 64}
    proof = {'verified': True, 'observed_at': time.time(), 'runtime_instance_id': 'rt-api',
        'transition_role': 'active', 'shared_state_write_authorized': True, 'slot': 'B', 'source': actual}
    observed = {'runtime_api_ready': True, 'runtime_instance_id': 'rt-api',
        'managed_runtime_api_identity': proof}
    if fault == 'wrong_package': actual['package_digest'] = 'sha256:' + 'e' * 64
    if fault == 'wrong_commit': actual['commit'] = 'b' * 40
    elif fault == 'wrong_instance': proof['runtime_instance_id'] = 'another-api'
    elif fault == 'wrong_slot': proof['slot'] = 'A'
    elif fault == 'unverified': proof['verified'] = False
    elif fault == 'expired': proof['observed_at'] -= 61
    elif fault == 'candidate': proof['transition_role'] = 'candidate'
    elif fault == 'write_fenced': proof['shared_state_write_authorized'] = False
    elif fault == 'not_ready': observed['runtime_api_ready'] = False
    elif fault == 'dirty_source': actual['source_dirty'] = True
    confirmed = confirm_ready_source('B', core_slots.read_slot_manifest('B'), serving_runtime=observed)
    assert confirmed is (fault is None)
    state = node_runtime_state.load_node_runtime_state()
    if confirmed:
        assert state['confirmed_core_release']['runtime_instance_id'] == 'rt-api'
        assert state['confirmed_core_release']['commit'] == 'a' * 40
        monkeypatch.setattr(runtime_identity, 'runtime_instance_id', lambda: 'rt-api')
        monkeypatch.setattr('adaos.build_info.runtime_source_identity', lambda: actual)
        assert source_policy.delivery_snapshot()['confirmed_current'] is True
    else:
        assert 'confirmed_core_release' not in state


@pytest.mark.parametrize('ready_proof', [True, False])
@pytest.mark.parametrize('already_succeeded', [True, False])
def test_supervisor_cannot_finish_or_clear_the_plan_without_the_serving_source_proof(
        update, monkeypatch, ready_proof, already_succeeded):
    import time
    plan, _ = update
    core_update.prepare_pending_update(plan)
    core_update.execute_pending_update(plan)
    core_update.write_plan(plan)
    core_update.write_status({**plan, 'state': 'succeeded' if already_succeeded else 'restarting',
        'phase': 'validate' if already_succeeded else 'launch'})
    monkeypatch.setattr(core_update, 'resolved_root_promotion_requirement', lambda _: (False, {}))
    observed = {'runtime_api_ready': True, 'runtime_instance_id': 'rt-api',
        'managed_runtime_api_identity': {'verified': True, 'observed_at': time.time(),
            'slot': 'B', 'transition_role': 'active', 'shared_state_write_authorized': True,
            'runtime_instance_id': 'rt-api', 'source': {'branch': 'main',
                'commit': ('a' if ready_proof else 'b') * 40, 'version': '1.0.0'}}}
    result = core_update.finalize_runtime_boot_status(supervisor_authorized=True,
        serving_runtime=observed, publish_events=False)
    assert result['state'] == ('succeeded' if ready_proof else 'restarting')
    assert (core_update.read_plan() is None) is ready_proof
    assert bool(result.get('source_confirmation_required')) is not ready_proof


def test_policy_bound_plan_survives_root_promotion_and_clears_stale_projection_after_readiness(update, monkeypatch):
    import time
    plan, _ = update
    prepared = core_update.prepare_pending_update(plan)
    core_update.execute_pending_update(prepared['plan'])
    core_update.write_plan(prepared['plan'])
    monkeypatch.setattr(core_update, 'resolved_root_promotion_requirement', lambda manifest: (
        True, {'effective_required': True, 'effective_mismatched_paths': ['changed.py']}))
    result = core_update.finalize_runtime_boot_status(supervisor_authorized=True, publish_events=False)
    assert result['state'] == 'validated' and result['phase'] == 'root_promotion_pending'
    assert core_update.read_plan() == prepared['plan']
    assert 'confirmed_core_release' not in node_runtime_state.load_node_runtime_state()
    monkeypatch.setattr(core_update, 'resolved_root_promotion_requirement', lambda manifest: (
        False, {'effective_required': False, 'effective_mismatched_paths': []}))
    observed = {'runtime_api_ready': True, 'runtime_instance_id': 'rt-api',
        'managed_runtime_api_identity': {'verified': True, 'observed_at': time.time(),
            'runtime_instance_id': 'rt-api', 'transition_role': 'active',
            'shared_state_write_authorized': True, 'slot': 'B',
            'source': {'branch': 'main', 'commit': 'a' * 40}}}
    result = core_update.finalize_runtime_boot_status(supervisor_authorized=True,
        serving_runtime=observed, publish_events=False)
    assert result['state'] == 'succeeded' and core_update.read_plan() is None
    assert result['root_promotion_required'] is False
    assert result['bootstrap_update']['effective_mismatched_paths'] == []


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
    from test_core_update import _make_valid_slot
    contract, observations, _ = composition
    _make_valid_slot('A')
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


@pytest.mark.parametrize('phase', ['before_prepare', 'during_prepare', 'after_prepare', 'during_command'])
def test_new_authenticated_desired_commit_expires_outgoing_unselected_work(update, monkeypatch, phase):
    plan, calls = update
    def supersede():
        source_policy.remember_desired_release({'branch': 'main', 'head_sha': 'b' * 40},
            policy=source_policy.source_policy())
    if phase == 'before_prepare':
        supersede()
    if phase == 'during_prepare':
        prepare = __import__('adaos.apps.core_update_apply', fromlist=['prepare_slot']).prepare_slot
        def changed(**kwargs):
            result = prepare(**kwargs)
            supersede()
            return result
        monkeypatch.setattr('adaos.apps.core_update_apply.prepare_slot', changed)
    prepared = core_update.prepare_pending_update(plan)
    if phase in {'before_prepare', 'during_prepare'}:
        result = prepared
    else:
        assert prepared['state'] == 'prepared'
        if phase == 'after_prepare':
            supersede()
        else:
            execute = core_update._run_command_with_bounded_output
            def changed(command):
                result = execute(command)
                supersede()
                return result
            monkeypatch.setattr(core_update, '_run_command_with_bounded_output', changed)
        result = core_update.execute_pending_update(prepared['plan'])
    assert result['state'] == 'failed' and result['error'] == 'desired_release_superseded'
    assert core_slots.selected_slot() == 'A'
    assert calls == (['command'] if phase == 'during_command' else [])


def test_supersession_still_recovers_the_original_already_selected_activation(update):
    plan, calls = update
    prepared = core_update.prepare_pending_update(plan)
    assert core_update.execute_pending_update(prepared['plan'])['state'] == 'succeeded'
    source_policy.remember_desired_release({'branch': 'main', 'head_sha': 'b' * 40},
        policy=source_policy.source_policy())
    result = core_update.execute_pending_update(prepared['plan'])
    assert result['state'] == 'restarting' and result['activation_reconciled']
    assert calls == ['command'] and core_slots.selected_slot() == 'B'


@pytest.mark.parametrize('desired', [None, 'same', 'other'])
def test_same_source_can_follow_new_policy_without_changing_the_artifact_or_activation(update, monkeypatch, desired):
    import time
    from adaos.services.core_update_admission import confirm_ready_source
    plan, calls = update
    core_update.prepare_pending_update(plan)
    core_update.execute_pending_update(plan)
    original = core_slots.slot_manifest_path('B').read_bytes()
    activation = node_runtime_state.load_node_runtime_state()['core_release_activation']
    monkeypatch.setenv('SRC_BRANCH', 'dev2027')
    source_policy.source_policy()
    monkeypatch.setenv('SRC_BRANCH', 'main')
    selected = source_policy.source_policy()
    if desired:
        source_policy.remember_desired_release({'branch': 'main',
            'head_sha': ('a' if desired == 'same' else 'b') * 40}, policy=selected)
    observed = {'runtime_api_ready': True, 'runtime_instance_id': 'rt-rebound',
        'managed_runtime_api_identity': {'verified': True, 'observed_at': time.time(),
            'slot': 'B', 'transition_role': 'active', 'shared_state_write_authorized': True,
            'runtime_instance_id': 'rt-rebound', 'source': {'branch': 'main', 'commit': 'a' * 40}}}
    assert confirm_ready_source('B', core_slots.read_slot_manifest('B'), serving_runtime=observed) is (desired == 'same')
    state = node_runtime_state.load_node_runtime_state()
    assert core_slots.slot_manifest_path('B').read_bytes() == original
    assert state['core_release_activation'] == activation and calls == ['command']
    if desired == 'same':
        assert state['confirmed_core_release']['policy'] == selected
        assert state['core_release_policy_binding']['original_policy'] == activation['policy']
        assert state['core_release_policy_binding']['policy'] == selected
        observed['runtime_instance_id'] = observed['managed_runtime_api_identity']['runtime_instance_id'] = 'rt-next-boot'
        assert confirm_ready_source('B', core_slots.read_slot_manifest('B'), serving_runtime=observed)
        assert node_runtime_state.load_node_runtime_state()['confirmed_core_release']['runtime_instance_id'] == 'rt-next-boot'
    else:
        assert 'confirmed_core_release' not in state
