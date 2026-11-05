from types import SimpleNamespace

import pytest

from adaos.services.applications.lifecycle import observe_runtime_admission
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
from adaos.services.applications.dashboard import project_application_dashboard


@pytest.mark.parametrize("source", ["stable_installation", "local_trial"])
def test_inventory_admission_observes_the_same_transition_fence_as_detail(tmp_path, monkeypatch, source):
    """A retained Beta lease cannot turn an unfinished Finalize green in inventory."""
    from adaos.services.applications import lifecycle

    selection = SimpleNamespace(application_id="test", source=source)
    selected_fact = {"admission": "admitted", "reason": "InstalledLocalBeta",
                     "admission_kind": "installed_local_beta", "expires_at": None,
                     "trial_expires_at": "2026-10-28T00:00:00+00:00", "remaining_seconds": None}
    monkeypatch.setattr(lifecycle, "_observe_selected_runtime_admission", lambda *a, **k: dict(selected_fact))
    monkeypatch.setattr(ApplicationRuntimeChannel, "pending_transitions", lambda self: ({"running_step": "publish_verify"},))
    observed = observe_runtime_admission(tmp_path, selection)
    assert observed["admission"] == "recovery_required"
    assert observed["reason"] == "RuntimeTransitionPending"
    assert observed["admission_kind"] == selected_fact["admission_kind"]
    assert observed["trial_expires_at"] == selected_fact["trial_expires_at"]


def test_inventory_admission_returns_to_selected_admission_after_fence_is_removed(tmp_path, monkeypatch):
    from adaos.services.applications import lifecycle

    selected = SimpleNamespace(application_id="test")
    fact = {"admission": "expiring", "reason": "TrialExpiring"}
    monkeypatch.setattr(lifecycle, "_observe_selected_runtime_admission", lambda *a, **k: dict(fact))
    monkeypatch.setattr(ApplicationRuntimeChannel, "pending_transitions", lambda self: ())
    assert observe_runtime_admission(tmp_path, selected) == fact


def release(version, digest):
    return {"version": version, "release_digest": digest}


def dashboard_model():
    return {"application": {"application_id": "app", "publisher_ref": "subnet:owner", "visibility": "public"},
            "channels": {"stable": "stable", "prerelease": "public-beta"},
            "active_release": release("0.1.48", "local-beta"),
            "marketplace_release": release("0.1.44", "stable"),
            "prerelease_release": release("0.1.47", "public-beta"),
            "runtime_revisions": {"alpha": {"version": "0.1.48", "status": "mutable_source"}},
            "lifecycle": {"runtime": {"release_digest": "local-beta", "source": "local_trial", "version": "0.1.48"},
                          "candidate": {"candidate_id": "candidate-48"},
                          "actions": {"open": {"eligible": False}, "promote_stable": {"eligible": False}},
                          "transition": {"status": "recovery_required", "operation_id": "application-stable:app:candidate-48"}},
            "local_development": {"trial": {"version": "0.1.38", "evidence_present": True},
                                  "publication": {"version": "0.1.65", "status": "published"},
                                  "source_registry": {"status": "published", "release_digest": "stable",
                                                      "application_catalog_publication": {"release_digest": "stable"}}}}


def test_dashboard_publication_takes_precedence_without_using_builder_history():
    value = project_application_dashboard(dashboard_model(), local_subnet_ref="subnet:owner")
    assert [p["id"] for p in value["positions"]] == ["private_alpha", "private_beta", "public_beta", "public_stable"]
    assert value["active_position_id"] == "private_beta"
    assert len([p for p in value["positions"] if p["active"]]) == 1
    assert value["positions"][1]["version"] == "0.1.48"
    assert not value["positions"][1]["actions"]["finalize"]["eligible"]
    assert value["operation"]["stable_version"] == "0.1.44"
    assert "0.1.65" not in str(value) and "0.1.38" not in str(value)


def test_recipient_without_dev_has_only_public_positions():
    model = dashboard_model()
    model["runtime_revisions"] = {"alpha": None}
    model["lifecycle"]["runtime"] = {"release_digest": "public-beta", "source": "stable_installation"}
    value = project_application_dashboard(model, local_subnet_ref="subnet:recipient")
    assert [p["id"] for p in value["positions"]] == ["public_beta", "public_stable"]
    assert value["active_position_id"] == "public_beta"


def test_finalize_replaces_the_accepted_private_beta_position():
    model = dashboard_model()
    model["channels"] = {"stable": "local-beta"}
    model["lifecycle"]["runtime"]["source"] = "stable_installation"
    model["lifecycle"].pop("transition")
    published = model["marketplace_release"]
    value = project_application_dashboard(model, local_subnet_ref="subnet:owner", public_stable=published)
    positions = {p["id"]: p for p in value["positions"]}
    assert "private_beta" not in positions and "public_beta" not in positions
    assert positions["private_stable"]["version"] == "0.1.48"
    assert positions["public_stable"]["version"] == "0.1.44"
    assert value["active_position_id"] == "private_stable"


def test_unpublished_private_stable_does_not_manufacture_publication():
    model = dashboard_model()
    model["channels"].pop("prerelease")
    model["local_development"]["source_registry"]["application_catalog_publication"]["release_digest"] = "another-release"
    value = project_application_dashboard(model, local_subnet_ref="subnet:owner")
    assert not any(p["scope"] == "public" for p in value["positions"])


def test_publication_actions_follow_the_private_release_stage():
    model = dashboard_model()
    model['lifecycle'].pop('transition')
    model['lifecycle']['candidate']['accepted'] = True
    model['lifecycle']['stable_candidate'] = {'candidate_id':'stable-candidate','release_digest':'stable'}
    value = project_application_dashboard(model, local_subnet_ref='subnet:owner',
        public_stable=release('0.1.40', 'older-public-stable'))
    rows = {row['id']:row for row in value['positions']}
    assert rows['private_beta']['actions']['publish_beta']['eligible']
    assert rows['private_beta']['actions']['publish_beta']['arguments']['candidate_id'] == 'candidate-48'
    stable = rows['private_stable']['actions']
    assert 'publish_beta' not in stable
    assert stable['publish_initial_stable']['eligible']
    assert stable['publish_initial_stable']['tool_id'] == 'applications.development.publish_to_registry'
    assert stable['publish_initial_stable']['arguments'] == {'release_digest':'stable'}


def test_queued_finalize_has_visible_indeterminate_progress_and_no_competing_beta_actions():
    model=dashboard_model()
    model['lifecycle'].pop('transition')
    model['lifecycle']['actions'].update(deactivate_beta={'eligible':True}, restore_stable={'eligible':True})
    model['lifecycle']['operation']={'operation_id':'confirmation-1','action':'promote_stable',
        'status':'applying','execution_status':'queued','revision':1}
    dashboard=project_application_dashboard(model,local_subnet_ref='subnet:owner')
    assert dashboard['busy']
    assert dashboard['operation']['task']['status']=='queued'
    assert 'progress' not in dashboard['operation']['task']
    beta=next(row for row in dashboard['positions'] if row['id']=='private_beta')
    assert not beta['actions']['remove_beta']['eligible'] and not beta['actions']['restore_stable']['eligible']
    model['lifecycle']['operation']['execution_status']='running'
    model['lifecycle']['transition']={'status':'applying','phase':'adopt_configuration',
        'completed_steps':['protect_and_adopt_data'],'total_steps':3,
        'steps':['protect_and_adopt_data','adopt_configuration','publish_verify']}
    task=project_application_dashboard(model,local_subnet_ref='subnet:owner')['operation']['task']
    assert task['progress']=={'completed':1,'total':3}
    assert [stage['status'] for stage in task['stages']]==['completed','running','pending']


def test_public_beta_can_be_the_same_release_as_private_stable_before_marketplace_promotion():
    model = dashboard_model()
    model['channels'] = {'stable': 'local-beta', 'prerelease': 'local-beta'}
    model['lifecycle']['runtime']['source'] = 'stable_installation'
    value = project_application_dashboard(model, local_subnet_ref='subnet:owner',
                                         public_stable=model['marketplace_release'])
    positions = {p['id']: p for p in value['positions']}
    assert 'private_stable' not in positions
    assert positions['public_beta']['version'] == '0.1.48'
    assert positions['public_stable']['version'] == '0.1.44'
    assert value['active_position_id'] == 'public_beta'


def test_exact_public_stable_promotion_retires_its_prerelease_position():
    model = dashboard_model()
    model['channels']['prerelease'] = 'stable'
    value = project_application_dashboard(model, local_subnet_ref='subnet:owner',
                                         public_stable=model['marketplace_release'])
    assert not any(p['id'] == 'public_beta' for p in value['positions'])


def test_retained_beta_remains_visible_while_stable_is_selected():
    model=dashboard_model()
    model['lifecycle']['runtime']={'source':'stable_installation','release_digest':'stable'}
    model['lifecycle']['installation']={'stable_release_digest':'stable','stable_baseline_present':True}
    model['lifecycle']['retained_betas']=[{'candidate_id':'candidate-48','release_digest':'local-beta',
        'version':'0.1.48','availability':'ready','available_at':'2026-11-01T00:00:00+00:00'}]
    model['lifecycle'].pop('transition')
    projected=project_application_dashboard(model,local_subnet_ref='subnet:owner')
    positions={item['id']:item for item in projected['positions']}
    beta=positions['private_beta']
    assert beta['version']=='0.1.48' and beta['active'] is False
    assert beta['available_at']=='2026-11-01T00:00:00+00:00'
    assert beta['actions']['open']['tool_id']=='applications.lifecycle.plan'
    assert beta['actions']['open']['arguments']=={'action':'select_runtime','target_position_id':'private_beta',
        'release_digest':'local-beta','candidate_id':'candidate-48'}
    assert beta['actions']['open']['mode']=='switch_and_open'
    assert projected['active_position_id']=='public_stable'


def test_distinct_private_stable_is_retained_even_if_its_version_matches_public():
    model=dashboard_model()
    model['channels']['stable']='private-digest'
    model['installed_release']=release('0.1.44','private-digest')
    value=project_application_dashboard(model,local_subnet_ref='subnet:owner',public_stable=model['marketplace_release'])
    assert len(value['positions'])==5
    assert next(row for row in value['positions'] if row['id']=='private_stable')['release_digest']=='private-digest'


def test_cached_foreign_development_never_manufactures_private_alpha():
    model=dashboard_model()
    projected=project_application_dashboard(model,local_subnet_ref='subnet:recipient')
    assert all(item['scope']=='public' for item in projected['positions'])


def test_selected_beta_commands_are_reviewable_exact_core_operations():
    from jsonschema import Draft202012Validator, ValidationError
    from adaos.services.applications.dashboard_contract import dashboard_schema
    model = dashboard_model()
    model['lifecycle'].pop('transition')
    model['lifecycle']['actions']['promote_stable'] = {'eligible': True, 'required_capability': 'applications.publish'}
    model['lifecycle']['revision'] = 'exact-lifecycle-revision'
    dashboard = project_application_dashboard(model, local_subnet_ref='subnet:owner')
    Draft202012Validator(dashboard_schema()).validate(dashboard)
    beta = dashboard['positions'][1]
    command = beta['actions']['finalize']
    assert command['eligible'] and command['requires_review']
    assert command['required_capability'] == 'applications.publish'
    assert command['tool_id'] == 'applications.lifecycle.finalize'
    assert command['arguments'] == {'application_id': 'app', 'candidate_id': 'candidate-48',
        'release_digest': 'local-beta', 'expected_revision': 'exact-lifecycle-revision'}
    command['tool_id'] = 'applications.lifecycle.plan'
    with pytest.raises(ValidationError):
        Draft202012Validator(dashboard_schema()).validate(dashboard)
    command['tool_id'] = 'applications.lifecycle.finalize'
    command['arguments'] = {}
    with pytest.raises(ValidationError):
        Draft202012Validator(dashboard_schema()).validate(dashboard)


def test_new_admitted_beta_is_an_update_without_changing_the_observed_selection():
    model = dashboard_model()
    model['lifecycle'].pop('transition')
    model['lifecycle']['retained_betas'] = [
        {'candidate_id': 'broken-51', 'release_digest': 'broken', 'version': '0.1.51', 'availability': 'unavailable'},
        {'candidate_id': 'candidate-50', 'release_digest': 'new-beta', 'version': '0.1.50', 'availability': 'ready',
         'available_at': '2026-11-03T10:00:00+00:00'},
        {'candidate_id': 'candidate-48', 'release_digest': 'local-beta', 'version': '0.1.48', 'availability': 'ready'}]
    projection = project_application_dashboard(model, local_subnet_ref='subnet:owner')
    beta = next(row for row in projection['positions'] if row['id'] == 'private_beta')
    assert beta['active'] and beta['version'] == '0.1.48'
    assert beta['update_version'] == '0.1.50'
    assert beta['actions']['update']['arguments'] == {'action': 'select_runtime', 'target_position_id': 'private_beta',
        'release_digest': 'new-beta', 'candidate_id': 'candidate-50'}
    assert projection['active_position_id'] == 'private_beta'
    model['lifecycle']['transition'] = {'status': 'applying'}
    busy = project_application_dashboard(model, local_subnet_ref='subnet:owner')
    assert not next(row for row in busy['positions'] if row['id'] == 'private_beta')['actions']['update']['eligible']


@pytest.mark.parametrize('version', ['0.1.9', '0.1.48', 'unknown'])
def test_older_or_unknown_beta_does_not_offer_an_update(version):
    model = dashboard_model()
    model['lifecycle']['retained_betas'] = [{'candidate_id': 'other', 'release_digest': 'other',
        'version': version, 'availability': 'ready'}]
    beta = project_application_dashboard(model, local_subnet_ref='subnet:owner')['positions'][1]
    assert 'update' not in beta['actions']


def test_dashboard_is_a_declared_contract_with_its_own_freshness_revision():
    from jsonschema import Draft202012Validator
    from adaos.services.applications.dashboard_contract import dashboard_schema
    model=dashboard_model()
    first=project_application_dashboard(model,local_subnet_ref='subnet:owner')
    Draft202012Validator(dashboard_schema()).validate(first)
    model['channels']['prerelease']='new-public-beta'
    model['prerelease_release']=release('0.1.49','new-public-beta')
    second=project_application_dashboard(model,local_subnet_ref='subnet:owner')
    assert second['lifecycle_revision']==first['lifecycle_revision']
    assert second['revision']!=first['revision']


def test_finalize_does_not_replace_the_retired_beta_row_with_an_older_retained_beta():
    model = dashboard_model()
    model['lifecycle'].pop('transition')
    model['channels']['stable'] = 'accepted-49'
    model['active_release'] = model['installed_release'] = release('0.1.49', 'accepted-49')
    model['lifecycle']['runtime'] = {'source': 'stable_installation', 'release_digest': 'accepted-49'}
    model['lifecycle']['retained_betas'] = [{'version': '0.1.48', 'candidate_id': 'old-48',
        'release_digest': 'local-beta', 'availability': 'ready'}]
    projected = project_application_dashboard(model, local_subnet_ref='subnet:owner')
    assert not any(row['id'] == 'private_beta' for row in projected['positions'])
    # An explicit selection of the older retained release must still be factual.
    model['active_release'] = release('0.1.48', 'local-beta')
    model['lifecycle']['runtime'] = {'source': 'local_trial', 'release_digest': 'local-beta'}
    selected = project_application_dashboard(model, local_subnet_ref='subnet:owner')
    assert next(row for row in selected['positions'] if row['id'] == 'private_beta')['active']


def test_already_offered_public_beta_is_not_a_new_publication_action():
    model = dashboard_model()
    model['lifecycle'].pop('transition')
    model['lifecycle']['stable_candidate'] = {'release_digest': 'stable'}
    model['channels']['prerelease'] = 'stable'
    model['prerelease_release'] = model['marketplace_release']
    projection = project_application_dashboard(model, local_subnet_ref='subnet:owner',
                                               public_stable=release('0.1.40', 'older-public'))
    assert not any(row['id']=='private_stable' for row in projection['positions'])
    beta = next(row for row in projection['positions'] if row['id']=='public_beta')
    assert 'publish_beta' not in beta['actions']
    assert beta['actions']['promote_public_stable']['eligible']


def test_ineligible_open_has_no_executable_mode_for_adaptive_controls():
    dashboard = project_application_dashboard(dashboard_model(), local_subnet_ref='subnet:owner')
    assert all('mode' not in row['actions']['open'] for row in dashboard['positions']
               if 'open' in row['actions'] and not row['actions']['open']['eligible'])
