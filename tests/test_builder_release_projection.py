from copy import deepcopy

from adaos.services.builder.release_projection import project_release_lineage


def test_installed_stable_is_not_an_editable_automation_head_or_checkpoint_action():
    nodes = [{'kind': 'stage', 'children': [{'kind': 'automation_result', 'status': 'historical',
        'version': '0.5.2', 'lineageInferred': True, 'canPreview': True, 'children': [
            {'kind': 'publication_release', 'release': 'exact', 'status': 'trial', 'lineageInferred': True,
             'canOpenPublication': True, 'version': '0.5.2'}]}]}]
    original = deepcopy(nodes)
    life = {'runtime': {'release_digest': 'exact', 'admission_kind': 'stable', 'version': '0.5.2'},
            'candidate': {'candidate_id': 'new'}}
    workflow = {'delivery': {'candidate_id': 'old'}, 'workflow_description': {
        'allowed_commands': [{'command': 'reconcile_publication'}]}}
    rows = project_release_lineage(nodes, lifecycle=life, workflow=workflow)
    source = rows[0]['children'][0]
    assert source['status'] == 'Source not linked' and not source['canPreview']
    assert source['children'][0]['title'] == 'Stable 0.5.2'
    assert not source['children'][0]['canOpenPublication']
    assert rows[0]['subtitle'] == 'Installed: Stable 0.5.2'
    assert nodes == original


def test_version_equality_never_grants_exact_selected_release_identity():
    rows = project_release_lineage([{'kind': 'publication_release', 'version': '1.0.0', 'release': 'other'}],
        lifecycle={'runtime': {'release_digest': 'exact', 'version': '1.0.0', 'admission_kind': 'stable'}}, workflow={})
    assert not rows[0].get('selected') and 'title' not in rows[0]
    assert not rows[0]['canOpenPublication']


def test_unretained_old_automation_never_previews_newer_mutable_dev_under_an_old_label():
    nodes = [{'kind': 'stage', 'version': '0.5.2', 'children': [
        {'kind': 'automation_result', 'version': '0.5.0', 'canPreview': True, 'canOpenAutomation': True}]}]
    projected = project_release_lineage(nodes, lifecycle={'runtime': {}}, workflow={'automation': {'result_version': '0.5.0'}})
    row = projected[0]['children'][0]
    assert not row['canPreview'] and not row['canOpenAutomation']
    assert row['status'] == 'Source snapshot unavailable'
    retained = project_release_lineage(nodes, lifecycle={'runtime': {}},
        workflow={'automation': {'result_version': '0.5.0', 'snapshot_path': 'immutable-snapshot'}})
    assert retained[0]['children'][0]['canPreview']
