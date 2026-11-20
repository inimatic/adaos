from copy import deepcopy
import json
import sqlite3

import pytest

from adaos.services.site_studio import (
    apply_change_set, authoring_projection, commit_revision, compile_site_revision, digest,
)
from adaos.services.site_studio.component_editor import component_editor, component_field_operations
from adaos.services.site_studio.contracts import SiteStudioContractError
from adaos.services.site_studio.publication_export import export_publication
from test_site_studio_authoring import CREATED, _source, _context, _change_set


def draft_source():
    source = _source('with-draft', CREATED)
    source['routes'][0]['sections'][1]['draft'] = True
    source['resources'][1]['data'].update(title='UNPUBLISHED PERSON', body='DRAFT CONTENT ONLY')
    source['resources'][0]['data']['items'] = [
        {'label': 'Hidden person', 'href': '#hero'},
        {'label': 'Published footer', 'href': '#footer'},
    ]
    raw = b'draft-only-owned-image'
    source['assets'] = [{'id': 'private-portrait', 'source_path': 'objects/private-image.png',
        'public_path': 'assets/private-image.png', 'media_type': 'image/png', 'digest': digest(raw)}]
    source['resources'][1]['data']['media'] = {'src': 'private-portrait', 'alt': 'UNPUBLISHED PERSON'}
    return source, raw


def test_drafts_stay_in_authoring_but_not_in_pages_resources_assets_or_navigation():
    source, raw = draft_source()
    before = deepcopy(source)
    compiled = compile_site_revision(source, asset_resolver=lambda _: raw)
    assert source == before and compiled['source'] == source
    preview = compiled['preview']
    assert [widget['id'] for widget in preview['page']['widgets']] == ['header', 'footer']
    assert preview['projection']['assets'] == []
    assert [ref['id'] for ref in preview['projection']['resources']] == ['navigation', 'footer']
    assert preview['page']['widgets'][0]['inputs']['data']['items'] == [
        {'label': 'Published footer', 'href': '#footer'}]
    assert 'resources/hero.json' not in compiled['artifacts']
    serialized = json.dumps(compiled['artifacts'])
    assert 'UNPUBLISHED PERSON' not in serialized and 'DRAFT CONTENT ONLY' not in serialized
    assert 'private-image.png' not in serialized and 'Hidden person' not in serialized
    editor = component_editor(source, 'hero', revision=1)
    assert editor['section_draft'] is True
    operations = component_field_operations(editor, {'section_draft': False})
    change = _change_set(source, value='unused')
    change['operations'] = operations
    restored = apply_change_set(source, change)
    assert 'draft' not in restored['routes'][0]['sections'][1]
    restored_preview = compile_site_revision(restored, asset_resolver=lambda _: raw)['preview']
    assert 'hero' in [widget['id'] for widget in restored_preview['page']['widgets']]
    assert restored['resources'][1] == source['resources'][1]


def test_publication_export_does_not_copy_draft_text_or_private_only_bytes(tmp_path):
    source, raw = draft_source()
    files = tmp_path / 'files'
    (files / 'objects').mkdir(parents=True)
    (files / 'objects/private-image.png').write_bytes(raw)
    database = tmp_path / 'authoring.sqlite3'
    with sqlite3.connect(database) as db:
        commit_revision(db, project_id='adaos-site', title='AdaOS', source=source,
                        authoring_context=_context(source),
                        change_set=_change_set(source, value=source['resources'][1]['data']['body']),
                        asset_resolver=lambda _: raw)
        tree = authoring_projection(db, 'adaos-site')
        assert tree['tree']['routes'][0]['sections'][1]['draft'] is True
    output = tmp_path / 'published'
    receipt = export_publication(database, files, output)
    assert receipt['source_digest'] == digest(source)
    assert not (output / 'assets/private-image.png').exists()
    assert not (output / 'resources/hero.json').exists()
    public_text = '\n'.join(path.read_text('utf-8') for path in output.rglob('*') if path.is_file())
    assert 'UNPUBLISHED PERSON' not in public_text and 'DRAFT CONTENT ONLY' not in public_text
    with sqlite3.connect(database) as db:
        retained = json.loads(db.execute('SELECT source_body FROM site_studio_revisions').fetchone()[0])
        assert retained == source


def test_shared_resource_and_assets_remain_when_a_live_section_still_uses_them():
    source, raw = draft_source()
    source['routes'][0]['sections'].insert(2, {'id': 'live-copy', 'component': 'site.hero',
        'resource_ref': 'hero', 'anchor_id': 'published-person'})
    compiled = compile_site_revision(source, asset_resolver=lambda _: raw)
    assert [w['id'] for w in compiled['preview']['page']['widgets']] == ['header', 'live-copy', 'footer']
    assert 'resources/hero.json' in compiled['artifacts']
    assert compiled['projection']['assets'][0]['id'] == 'private-portrait'
    source['routes'][0]['sections'].pop(2)
    source['identity']['favicon_asset_ref'] = 'private-portrait'
    assert compile_site_revision(source, asset_resolver=lambda _: raw)['projection']['assets'][0]['id'] == 'private-portrait'


@pytest.mark.parametrize('value', [0, 1, 'true', None])
def test_draft_flag_rejects_non_boolean_edits(value):
    source = _source('invalid-draft', CREATED)
    change = _change_set(source, value='unused')
    change['operations'] = [{'operation_id': 'draft', 'kind': 'set_section_field',
        'target': 'routes.landing.sections.hero.draft', 'value': value}]
    with pytest.raises(SiteStudioContractError, match='boolean'):
        apply_change_set(source, change)
    source['routes'][0]['sections'][1]['draft'] = value
    with pytest.raises(SiteStudioContractError, match='draft'):
        compile_site_revision(source)


def test_draft_editor_is_at_the_section_root_and_keeps_nested_fields_scoped():
    source = _source('draft-editor', CREATED)
    editor = component_editor(source, 'hero', revision=1)
    assert editor['section_draft'] is False
    changed = component_field_operations(editor, {'section_draft': True})
    assert changed[0]['target'] == 'routes.landing.sections.hero.draft'
    assert 'section_draft' not in component_editor(source, 'hero', revision=1, node_path='actions')
    with pytest.raises(ValueError, match='type'):
        component_field_operations(editor, {'section_draft': 'true'})
