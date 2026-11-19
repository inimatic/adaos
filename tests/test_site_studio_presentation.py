from copy import deepcopy

import pytest

from adaos.services.site_studio import compile_site_revision
from adaos.services.site_studio.chat_authoring import build_chat_context
from adaos.services.site_studio.chat_authoring import read_chat_source
from adaos.services.site_studio.contracts import SiteStudioContractError, digest
from test_site_studio_authoring import CREATED, _source, _change_set


def artwork():
    return {'viewBox': '0 0 200 100', 'label': 'A different organization', 'nodes': [
        {'tag': 'path', 'attrs': {'d': 'M10 80 L100 10 L190 80', 'fill': 'none', 'stroke': 'accent',
                                'stroke-dasharray': 250, 'stroke-dashoffset': 250},
         'children': [{'tag': 'animate', 'attrs': {'attributeName': 'stroke-dashoffset', 'from': 250, 'to': 0,
                                                'begin': '0.4s', 'dur': '1.9s', 'fill': 'freeze'}}]},
    ]}


def test_reference_presentation_preserves_source_anchors_data_and_plain_text():
    source = _source('presentation', CREATED)
    source['theme']['tokens'].update({'font.family': 'light-sans', 'font.bodyWeight': 300, 'font.headingWeight': 200})
    source['resources'][0]['data']['variant'] = 'drawer'
    source['resources'][1]['data'].update(variant='intro', artwork=artwork(), scrollCue=True,
                                        presentation={'style': 'editorial', 'reveal': 'stagger'})
    before = deepcopy(source)
    compiled = compile_site_revision(source)
    assert source == before
    page = compiled['preview']['page']
    assert page['widgets'][1]['inputs']['data']['artwork'] == artwork()
    assert page['widgets'][1]['inputs']['sourceAnchor'] == 'routes.landing.sections.hero'
    assert compiled['preview']['source']['resources'] == source['resources']
    assert 'M10 80 L100 10 L190 80' not in compiled['preview']['route']['visible_content']
    assert compiled['preview']['projection']['theme']['css_variables']['--ada-site-body-weight'] == '300'
    assert 'Helvetica Neue' in compiled['preview']['projection']['theme']['css_variables']['--ada-site-font-family']


@pytest.mark.parametrize('mutate', [
    lambda art: art['nodes'][0].update(tag='script'),
    lambda art: art['nodes'][0]['attrs'].update(onload='alert(1)'),
    lambda art: art['nodes'][0]['attrs'].update(stroke='url(https://example.com/paint)'),
    lambda art: art['nodes'][0]['children'][0]['attrs'].update(begin='click'),
    lambda art: art['nodes'][0]['children'][0]['attrs'].update(attributeName='href'),
    lambda art: art.update(viewBox='0 0 NaN 100'),
])
def test_geometry_cannot_expand_into_active_content(mutate):
    source = _source('denied-artwork', CREATED)
    value = artwork()
    mutate(value)
    source['resources'][1]['data'].update(variant='intro', artwork=value)
    with pytest.raises(SiteStudioContractError):
        compile_site_revision(source)


def test_numeric_overshoot_easing_is_admitted_without_executable_styles():
    from adaos.services.site_studio.presentation import component_presentation_data
    value = artwork()
    animation = value['nodes'][0]['children'][0]['attrs']
    animation.update(easing='cubic-bezier(.2,.9,.3,1.25)')
    assert component_presentation_data('site.hero', {'artwork': value})['artwork'] == value
    for curve in ('cubic-bezier(2,0,1,1)', 'cubic-bezier(0,0,1,NaN)', 'url(external)', 'cubic-bezier(0,0,1,5)'):
        animation['easing'] = curve
        with pytest.raises(ValueError):
            component_presentation_data('site.hero', {'artwork': value})


def test_reference_wordmark_uses_the_same_bounded_svg_contract_in_narrative():
    from adaos.services.site_studio.component_editor import component_editor
    source = _source('vector-wordmark', CREATED)
    section = {'id': 'narrative', 'component': 'site.section', 'resource_ref': 'narrative-data'}
    resource = {'id': 'narrative-data', 'type': 'site.narrative', 'public': True,
                'data': {'title': 'Another organization', 'paragraphs': ['Editable copy']}}
    source['routes'][0]['sections'].insert(2, section)
    source['resources'].append(resource)
    resource['data']['lockup'] = {'artwork': artwork()}
    resource['data']['lockup']['artwork']['nodes'].append({'tag': 'text', 'attrs': {'x': 10, 'y': 40,
        'font-family': 'Helvetica Neue,-apple-system,sans-serif', 'font-weight': 100}, 'text': 'Another organization'})
    compiled = compile_site_revision(source)
    widget = next(widget for widget in compiled['preview']['page']['widgets'] if widget['id'] == section['id'])
    assert widget['inputs']['data']['lockup']['artwork'] == resource['data']['lockup']['artwork']
    with pytest.raises(ValueError, match='edited through the site chat'):
        component_editor(source, section['id'], revision=1, node_path='lockup.artwork.nodes')
    resource['data']['lockup']['artwork']['nodes'][0]['children'][0]['attrs']['begin'] = 'click'
    with pytest.raises(SiteStudioContractError):
        compile_site_revision(source)


def test_presentation_rejects_invented_behavior_and_theme_expressions():
    source = _source('denied-presentation', CREATED)
    source['resources'][1]['data']['presentation'] = {'style': 'foundation-specific', 'execute': 'code'}
    with pytest.raises(SiteStudioContractError):
        compile_site_revision(source)
    source['resources'][1]['data'].pop('presentation')
    source['theme']['tokens']['font.family'] = 'url(https://example.com/font)'
    with pytest.raises(SiteStudioContractError, match='font.family'):
        compile_site_revision(source)


def test_svg_coordinates_do_not_admit_active_channels_elsewhere():
    source = _source('svg-coordinate-boundary', CREATED)
    source['resources'][1]['data'].update(variant='intro', artwork={
        'viewBox': '0 0 100 100', 'label': 'Static text', 'nodes': [
            {'tag': 'g', 'attrs': {}, 'children': [{'tag': 'text', 'attrs': {'x': 5, 'y': 40}, 'text': 'A'}]}]})
    compile_site_revision(source)
    source['resources'][1]['data']['y'] = {'doc': 'active-channel'}
    with pytest.raises(SiteStudioContractError, match='forbidden static field'):
        compile_site_revision(source)


def test_intro_cannot_hide_all_content_without_an_owned_illustration():
    source = _source('empty-intro', CREATED)
    data = source['resources'][1]['data']
    data.pop('media', None)
    data['variant'] = 'intro'
    with pytest.raises(SiteStudioContractError, match='requires owned artwork or media'):
        compile_site_revision(source)


def test_chat_discovers_supported_design_capabilities_without_a_renderer_checkout():
    source = _source('discover-presentation', CREATED)
    packet = build_chat_context(source, project_id=source['id'], source_digest=digest(source),
                                target={}, user_text='Reproduce the supplied prototype')
    hero = next(c for c in packet['components'] if c['id'] == 'site.hero')
    assert 'variant=intro' in hero['description']
    assert packet['theme_capabilities']['font.family'] == ['system', 'light-sans']
    assert packet['theme'] == source['theme']


def test_unchanged_sites_do_not_acquire_an_editorial_profile_or_artwork():
    source = _source('standard-presentation', CREATED)
    compiled = compile_site_revision(source)
    assert compiled['preview']['source'] == source
    assert not any('presentation' in w['inputs']['data'] or 'artwork' in w['inputs']['data'] for w in compiled['preview']['page']['widgets'])


def test_content_height_and_spacing_are_typed_editable_and_preserved_in_preview():
    from adaos.services.site_studio.component_editor import component_editor, component_field_operations
    source = _source('compact-narrative', CREATED)
    source['resources'].append({'id': 'experts', 'type': 'site.narrative', 'public': True,
                                'data': {'title': 'Experts', 'paragraphs': ['Membership pending.']}})
    section = {'id': 'experts', 'component': 'site.section', 'resource_ref': 'experts'}
    source['routes'][0]['sections'].append(section)
    resource = next(r for r in source['resources'] if r['id'] == section['resource_ref'])
    resource['data']['presentation'] = {'style': 'editorial', 'height': 'content', 'paddingTop': 0, 'paddingBottom': 24}
    compiled = compile_site_revision(source)
    assert compiled['preview']['source']['resources'] == source['resources']
    editor = component_editor(source, section['id'], revision=1, node_path='presentation')
    height = next(k for k, p in editor['field_paths'].items() if p == 'presentation.height')
    assert next(f for f in editor['fields'] if f['id'] == height)['type'] == 'singleChoice'
    operation = component_field_operations(editor, {height: 'viewport'})[0]
    assert operation['target'].endswith('.data.presentation.height')
    assert operation['value'] == 'viewport'
    for field, value in [('height', '1000px'), ('paddingTop', -1), ('paddingBottom', 241), ('paddingTop', '0')]:
        invalid = deepcopy(source)
        next(r for r in invalid['resources'] if r['id'] == resource['id'])['data']['presentation'][field] = value
        with pytest.raises(SiteStudioContractError):
            compile_site_revision(invalid)


def test_collection_note_alignment_is_bounded_and_discoverable():
    from adaos.services.site_studio import default_component_catalog
    from adaos.services.site_studio.presentation import component_presentation_data
    data = {'presentation': {'noteAlign': 'center', 'paddingTop': 12, 'paddingBottom': 0}}
    assert component_presentation_data('site.cardGrid', data) == data
    contract = next(c for c in default_component_catalog()['components'] if c['id'] == 'site.cardGrid')
    assert 'noteAlign' in contract['editor']['description']
    assert next(f for f in contract['editor']['fields'] if f['path'] == 'presentation.noteAlign')['options'] == ['left', 'center', 'right']
    with pytest.raises(ValueError, match='Invalid component presentation'):
        component_presentation_data('site.cardGrid', {'presentation': {'noteAlign': 'arbitrary-css'}})


def test_ordinary_property_controls_preserve_the_chat_authored_illustration():
    from adaos.services.site_studio.component_editor import component_editor, component_field_operations
    from adaos.services.site_studio import apply_change_set
    source = _source('content-and-presentation', CREATED)
    source['resources'][1]['data'].update(variant='intro', artwork=artwork())
    editor = component_editor(source, 'hero', revision=1)
    assert 'artwork' not in [node['id'] for node in editor['tree'][0]['children']]
    title = next(key for key, path in editor['field_paths'].items() if path == 'title')
    operations = component_field_operations(editor, {title: 'Edited manually'})
    assert operations[0]['target'] == 'resources.hero.data.title'
    change = _change_set(source, value='Edited manually')
    change['operations'] = operations
    changed = apply_change_set(source, change)
    assert changed['resources'][1]['data']['title'] == 'Edited manually'
    assert changed['resources'][1]['data']['artwork'] == artwork()
    with pytest.raises(ValueError, match='edited through the site chat'):
        component_editor(source, 'hero', revision=1, node_path='artwork.nodes.0')


def test_site_context_supplies_small_resources_without_copying_the_dataset():
    source = _source('bounded-site-context', CREATED)
    source['resources'].append({'id': 'territories', 'type': 'site.dashboard', 'public': True,
                                'data': {'title': 'Territories', 'rows': [{'private_marker': 'dataset-value'}] * 10000}})
    packet = build_chat_context(source, project_id=source['id'], source_digest=digest(source), target={}, user_text='Restyle the site')
    summary = next(item for item in packet['resources'] if item['id'] == 'territories')
    assert summary['data'] == {'title': 'Territories'}
    assert summary['rows']['count'] == 10000
    assert summary['rows']['read']['target'] == 'resources.territories.data.rows'
    with pytest.raises(ValueError, match='Source path does not resolve: resources.missing.data'):
        read_chat_source(source, 'resources.missing.data')
