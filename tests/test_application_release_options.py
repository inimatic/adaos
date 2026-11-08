from adaos.services.applications.release_contract import with_public_release_options, release_list_result_schema
from jsonschema import Draft202012Validator


def test_private_channel_does_not_become_a_public_offer():
    releases = [{'application_id': 'app', 'version': version, 'release_digest': digest, 'channels': channels}
        for version, digest, channels in [('1.0.0', 'public', []), ('1.1.0', 'private', ['stable']),
                                         ('1.2.0', 'preview', ['prerelease'])]]
    model = {'installed': True, 'installation': {'status': 'active'}, 'dashboard': {'positions': [
        {'id': 'private_stable', 'scope': 'private', 'release_digest': 'private'},
        {'id': 'public_stable', 'scope': 'public', 'release_digest': 'public'},
        {'id': 'public_beta', 'scope': 'public', 'release_digest': 'preview'}]}}
    result = with_public_release_options(releases, model)
    Draft202012Validator(release_list_result_schema()).validate({'releases': result})
    assert result[0]['public_stable_available'] is True and result[0]['channels'] == []
    assert result[1]['public_stable_available'] is False
    assert result[2]['public_beta_available'] is True
    assert all(row['install_kind'] == 'update' for row in result)


def test_absent_public_positions_offer_no_private_release_to_recipients():
    rows = with_public_release_options([{'release_digest': 'private', 'channels': ['stable']}],
        {'installed': False, 'dashboard': {'positions': [
            {'id': 'private_stable', 'scope': 'private', 'release_digest': 'private'}]}})
    assert rows[0]['public_stable_available'] is False and rows[0]['public_beta_available'] is False
    assert rows[0]['install_kind'] == 'install'


def test_beta_only_installation_does_not_plan_an_update_of_absent_stable():
    from adaos.services.applications.release_contract import installation_plan_kind
    assert installation_plan_kind({'installed': True}) == 'install'
    assert installation_plan_kind({'installed': True, 'installation': {'status': 'removed'}}) == 'install'
