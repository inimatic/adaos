from adaos.services.applications.presentation import catalog_icon
from adaos.sdk.applications import _compact_catalog_output


def test_catalog_title_fallback_survives_summary_without_overriding_explicit_artwork():
    fallback = catalog_icon('Site Studio')
    assert fallback == {'icon': 'apps-outline', 'iconText': 'SS'}
    summary = _compact_catalog_output({'application': {'display': {'title': 'Site Studio'}}, **fallback})
    assert summary['iconText'] == 'SS'
    assert catalog_icon('Site Studio', 'folder-outline') == {'icon': 'folder-outline'}
    assert catalog_icon('Site Studio', 'apps-outline') == {'icon': 'apps-outline'}
    assert catalog_icon('Site Studio', '', 'image-outline')['icon'] == 'image-outline'
