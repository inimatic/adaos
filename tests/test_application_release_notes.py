from adaos.services.applications.release_notes import publication_notes


def operation(**patch):
    return {'application_id': 'app', 'subnet_ref': 'subnet:owner', 'status': 'succeeded',
        'action': 'publish_to_registry', 'updated_at': '2026-11-03T10:00:00Z',
        'intent': {'release_digest': 'exact', 'release_notes': 'Published changes'}, **patch}


def test_notes_are_exact_successful_publication_evidence_without_private_drafts():
    records = [operation(), operation(application_id='other'), operation(subnet_ref='subnet:other'),
        operation(status='unknown'), operation(status='failed'), operation(action='create_trial')]
    assert publication_notes(records, application_id='app', publisher_ref='subnet:owner') == {'exact': 'Published changes'}
    assert publication_notes(records[1:], application_id='app', publisher_ref='subnet:owner') == {}


def test_latest_explicit_empty_notes_do_not_resurrect_older_text():
    records = [operation(), operation(updated_at='2026-11-03T11:00:00Z',
        intent={'release_digest': 'exact', 'release_notes': ''})]
    assert publication_notes(records, application_id='app', publisher_ref='subnet:owner') == {'exact': ''}


def test_read_projection_preserves_notes_and_normal_missing_state():
    from adaos.sdk.applications import _release_read_model
    assert _release_read_model({})['notes_status'] == 'missing'
    assert _release_read_model({'release_notes': 'Exact notes'})['release_notes'] == 'Exact notes'
