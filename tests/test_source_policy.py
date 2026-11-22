from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from adaos.services import source_policy as policy
from adaos.services.node_runtime_state import load_node_runtime_state, save_node_runtime_state


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("ENV_TYPE", "prod")
    monkeypatch.delenv("SRC_BRANCH", raising=False)


def node(role="hub", hub="sn_test"):
    return SimpleNamespace(role=role, subnet_id=hub)


def test_inactive_slot_is_not_a_prepared_release_without_an_exact_update_intent():
    slots = {'active_slot': 'A', 'slots': {'B': {'manifest': {
        'source_branch': 'main', 'git_commit': 'b' * 40}}}}
    selected = {'branch': 'main'}
    assert policy._prepared_slot(slots, {'state': 'succeeded'}, selected) == {}
    assert policy._prepared_slot(slots, {'state': 'prepared', 'target_slot': 'B'}, selected) == {}
    exact = {'state': 'prepared', 'target_slot': 'B', 'target_version': 'b' * 40}
    assert policy._prepared_slot(slots, exact, selected)['commit'] == 'b' * 40
    assert policy._prepared_slot(slots, exact, {'branch': 'dev2027'}) == {}


def test_desired_release_rejects_reordered_and_conflicting_notifications(monkeypatch):
    selected = policy.source_policy(node())
    def release(commit="a" * 40, run=12):
        return {"branch": "main", "head_sha": commit, "qualification": {
            "source_branch": "main", "head_sha": commit, "run_id": run, "run_attempt": 1}}
    first = policy.remember_desired_release(release(), policy=selected)
    assert policy.remember_desired_release(release(), policy=selected) == first
    with pytest.raises(policy.SourcePolicyError, match="stale"):
        policy.remember_desired_release(release(run=11), policy=selected)
    with pytest.raises(policy.SourcePolicyError, match="conflict"):
        policy.remember_desired_release(release(commit="b" * 40), policy=selected)
    monkeypatch.setenv("SRC_BRANCH", "dev2027")
    policy.source_policy(node())
    with pytest.raises(policy.SourcePolicyError, match="policy_changed"):
        policy.remember_desired_release(release(), policy=selected)


def test_default_main_ignores_installed_provenance_and_legacy_aliases(monkeypatch):
    from adaos.services.root.core_update_sync import _named_core_update_branch
    monkeypatch.setenv("ADAOS_CORE_UPDATE_BRANCH", "unrelated")
    monkeypatch.setenv("ADAOS_REV", "unrelated")
    conf = node()
    assert _named_core_update_branch({"source_branch": "dev2027"}, conf) == "main"
    assert policy.source_policy(conf)["revision"] == 1


@pytest.mark.parametrize('active_commit,confirmed_current', [('a'*40, True), ('b'*40, False)])
def test_delivery_reports_confirmed_release_separately_from_active(monkeypatch, active_commit, confirmed_current):
    from adaos.services.node_runtime_state import mutate_node_runtime_state
    selected = policy.source_policy(node())
    monkeypatch.setattr('adaos.services.runtime_identity.runtime_instance_id',lambda:'this-boot')
    receipt = {'branch':'main', 'commit':'a'*40, 'policy':selected, 'state':'confirmed','runtime_instance_id':'this-boot'}
    mutate_node_runtime_state(lambda state: state.update(confirmed_core_release=receipt))
    monkeypatch.setattr('adaos.build_info.runtime_source_identity', lambda: {
        'branch':'main', 'commit':active_commit, 'version':'1.0.0'})
    monkeypatch.setattr('adaos.services.core_slots.slot_status', lambda: {})
    monkeypatch.setattr('adaos.services.core_update.read_status', lambda: {})
    snapshot = policy.delivery_snapshot(node())
    assert snapshot['confirmed'] == receipt
    assert snapshot['active']['commit'] == active_commit
    assert snapshot['confirmed_current'] is confirmed_current


def test_old_readiness_receipt_does_not_confirm_a_new_boot_of_the_same_commit(monkeypatch):
    from adaos.services.node_runtime_state import mutate_node_runtime_state
    selected=policy.source_policy(node())
    mutate_node_runtime_state(lambda state:state.update(confirmed_core_release={
        'branch':'main','commit':'a'*40,'policy':selected,'runtime_instance_id':'previous-boot'}))
    monkeypatch.setattr('adaos.services.runtime_identity.runtime_instance_id',lambda:'new-boot')
    monkeypatch.setattr('adaos.build_info.runtime_source_identity',lambda:{'branch':'main','commit':'a'*40})
    monkeypatch.setattr('adaos.services.core_slots.slot_status',lambda:{})
    monkeypatch.setattr('adaos.services.core_update.read_status',lambda:{})
    assert policy.delivery_snapshot(node())['confirmed_current'] is False


def test_policy_is_idempotent_and_branch_changes_advance_revision(monkeypatch):
    first = policy.source_policy(node())
    assert policy.source_policy(node()) == first
    monkeypatch.setenv("SRC_BRANCH", "dev2027")
    changed = policy.source_policy(node())
    assert changed["revision"] == first["revision"] + 1
    assert changed["branch"] == "dev2027"


def test_member_inherits_hub_ignores_local_env_and_retains_policy_offline(monkeypatch):
    incoming = policy.source_policy(node())
    monkeypatch.setenv("SRC_BRANCH", "dev2027")
    assert policy.inherit_source_policy(incoming, node("member")) == incoming
    assert policy.source_policy(node("member"))["branch"] == "main"
    assert policy.inherit_source_policy(incoming, node("member")) == incoming
    assert policy.source_policy(node("member", "sn_other"))["confirmed"] is False


@pytest.mark.parametrize("change,error", [
    ({"hub_id": "sn_foreign"}, "authority_mismatch"),
    ({"revision": True}, "authority_mismatch"),
    ({"confirmed": False}, "authority_mismatch"),
    ({"revision": 0}, "authority_mismatch"),
    ({"branch": "dev2027"}, "revision_conflict"),
])
def test_member_rejects_forged_or_conflicting_policy(change, error):
    incoming = policy.source_policy(node())
    policy.inherit_source_policy(incoming, node("member"))
    with pytest.raises(policy.SourcePolicyError, match=error):
        policy.inherit_source_policy({**incoming, **change}, node("member"))
    assert policy.source_policy(node("member")) == incoming


def test_old_message_cannot_revert_branch_change(monkeypatch):
    old = policy.source_policy(node())
    monkeypatch.setenv("SRC_BRANCH", "dev2027")
    new = policy.source_policy(node())
    with pytest.raises(policy.SourcePolicyError, match="stale"):
        policy.inherit_source_policy(old, node("member"))
    assert policy.source_policy(node("member")) == new


def test_policy_and_identity_writers_do_not_lose_each_others_fields():
    def write(index):
        if index % 2:
            save_node_runtime_state(hub_url="https://hub.example")
        else:
            policy.source_policy(node())
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(write, range(24)))
    saved = load_node_runtime_state()
    assert saved["hub_url"] == "https://hub.example"
    assert saved["source_policy"]["revision"] == 1


@pytest.mark.parametrize("branch,commit,error", [
    ("dev2027", "a" * 40, "branch_mismatch"),
    ("main", "abc1234", "exact_commit_required"),
    ("main", "0.3.49", "exact_commit_required"),
])
def test_release_requires_selected_branch_and_exact_commit(branch, commit, error):
    with pytest.raises(policy.SourcePolicyError, match=error):
        policy.verify_source_release(branch, commit, conf=node())


def test_new_member_cannot_install_before_hub_policy_confirmation():
    with pytest.raises(policy.SourcePolicyError, match="not_confirmed"):
        policy.verify_source_release("main", "a" * 40, conf=node("member"))


def test_developer_controls_branch_and_does_not_adopt_hub_override(monkeypatch):
    incoming = policy.source_policy(node())
    monkeypatch.setenv("ENV_TYPE", "dev")
    monkeypatch.setenv("SRC_BRANCH", "feature/authoring")
    selected = policy.inherit_source_policy(incoming, node("member"))
    assert selected["branch"] == "feature/authoring"
    assert selected["authority"] == "developer"


@pytest.mark.parametrize("value", ["HEAD", "../main", "a..b", "a.lock", "x/", "a b", "a@{b", "a" * 40])
def test_invalid_branch_cannot_be_selected(value):
    with pytest.raises(policy.SourcePolicyError):
        policy.validate_source_branch(value)
