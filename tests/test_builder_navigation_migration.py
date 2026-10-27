import ast
import pytest

from tools.builder_navigation_migration import upgrade_catalog_source, upgrade_observation_source, upgrade_webui


def test_builder_declares_entity_address_and_interactive_picker_without_losing_work():
    original = {"ui": {"application": {
        "desktop": {"pageSchema": {"initialStateSource": {"kind": "y", "path": "data/builder/selection"},
            "widgets": [{"id": "useful-existing-work"}]}},
        "modals": {"project-picker": {"schema": {"widgets": [{"id": "project-picker-table",
            "dataSource": {"name": "builder.list_projects"}}]}}}}}}
    upgraded = upgrade_webui(original)
    page = upgraded["ui"]["application"]["desktop"]["pageSchema"]
    assert page["initialStateSource"]["required"] is True
    assert page["initialStateSource"]["map"]["applicationTitle"] == "title"
    assert page["widgets"] == [{"id": "useful-existing-work"}]
    assert {entry["queryKey"] for entry in page["navigationState"]["bindings"]} == {
        "builder_object_id", "builder_object_type"}
    assert upgrade_webui(upgraded) == upgraded
    assert "navigationState" not in original["ui"]["application"]["desktop"]["pageSchema"]


def test_catalog_upgrade_uses_projected_metadata_not_per_member_sdk_resolution():
    source = '''def _catalog_updated(item, state):
    candidates = [item.get("created_at"), state.get("updated_at")]
    for ref in item.get("component_refs") or []:
        identity = _split_component_ref(str(ref))
        if identity:
            candidates.append(_catalog_state(*identity).get("updated_at"))
    return candidates
'''
    upgraded = upgrade_catalog_source(source)
    namespace = {}
    exec(compile(ast.parse(upgraded), "catalog", "exec"), namespace)
    assert namespace["_catalog_updated"]({"updated": "latest", "component_refs": ["skill:never-call"]}, {}) == ["latest", None, None]
    assert upgrade_catalog_source(upgraded) == upgraded
    with pytest.raises(ValueError, match="refusing"):
        upgrade_catalog_source("unknown_source")


def test_builder_observation_reuses_the_exact_snapshot_instead_of_scanning_twice():
    source = '''def get_runtime_revisions(application_id, room):
    return {**applications.get_runtime_revisions(application_id, webspace_id=room),
            "lifecycle": applications.get_lifecycle(application_id, webspace_id=room)}
'''
    upgraded = upgrade_observation_source(source)
    assert upgraded.count("applications.get_runtime_revisions(") == 1
    assert "runtime_revisions=revisions" in upgraded
    assert upgrade_observation_source(upgraded) == upgraded
