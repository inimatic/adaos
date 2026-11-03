from types import SimpleNamespace

import pytest

from adaos.services.applications import runtime_selection as selection


@pytest.mark.parametrize('root,kind,expected', [('trial:candidate', 'stable', 'beta'),
                                              ('workspace', 'stable', 'stable'),
                                              ('workspace', 'prerelease', 'beta')])
def test_presentation_uses_application_release_instead_of_component_or_channel_notice(monkeypatch, root, kind, expected):
    owner = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: '.unused'))
    authority = SimpleNamespace(application_id='suite', release_digest='exact', runtime_root_ref=root)
    monkeypatch.setattr(selection, 'selected_application', lambda *a: authority)
    monkeypatch.setattr(selection, 'ApplicationStore', lambda *a: SimpleNamespace(
        get_verified_release_projection=lambda *a: {'version': '0.1.48'}))
    from adaos.services.applications import lifecycle
    monkeypatch.setattr(lifecycle, 'workspace_release_kind', lambda *a: kind)
    result = selection.presentation_metadata(owner, 'desktop', 'scenario', 'component', source_mode='workspace')
    assert result == {'id': 'suite', 'version': '0.1.48', 'stage': expected, 'release_digest': 'exact'}


def test_legacy_component_without_application_has_no_invented_product_version(monkeypatch):
    monkeypatch.setattr(selection, 'selected_application', lambda *a: None)
    assert selection.presentation_metadata(None, 'desktop', 'scenario', 'legacy', source_mode='workspace') is None


def test_dev_presentation_uses_the_owning_project_version(monkeypatch):
    from adaos.sdk.developer import compositions
    monkeypatch.setattr(compositions, 'project_for_component', lambda ref: (
        {'id': 'suite', 'version': '0.1.49'} if ref == 'scenario:component' else None))
    assert selection.presentation_metadata(SimpleNamespace(), 'preview', 'scenario', 'component', source_mode='dev') == {
        'id': 'suite', 'version': '0.1.49', 'stage': 'alpha'}
