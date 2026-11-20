from copy import deepcopy
import json
import sqlite3

import pytest

from adaos.services.site_studio import commit_revision, digest, project_state
from adaos.services.site_studio.chat_authoring import CHAT_OUTPUT_SCHEMA, build_chat_context, propose_chat_change, read_chat_source
from adaos.services.site_studio.component_editor import component_editor, component_field_operations
from adaos.services.site_studio.source_materials import (
    delete_source_file, read_source_file, save_source_file, source_files,
)
from test_site_studio_authoring import CREATED, _change_set, _context, _source


def seeded():
    db = sqlite3.connect(":memory:")
    source = _source("revision-one", CREATED)
    commit_revision(db, project_id="adaos-site", title="AdaOS", source=source,
                    authoring_context=_context(source), change_set=_change_set(source, value="unchanged"))
    return db, source


def decision(target="resources.hero.data.body", value="Changed through the customer chat"):
    return {"reply": "Prepared", "action": "propose", "reads": [], "operations": [
        {"kind": "set_resource_field", "target": target, "value_json": json.dumps(value), "before": None, "after": None}],
        "proposed_description": None, "proposal_reason": "Owner requested this change"}


def test_response_grammar_separates_reply_inspection_and_proposal():
    from jsonschema import Draft202012Validator, ValidationError
    validator = Draft202012Validator(CHAT_OUTPUT_SCHEMA)
    proposal = decision()
    validator.validate(proposal)
    read = {"kind": "source", "target": "resources.hero", "offset": 0}
    for invalid in [{**proposal, "action": "reply"}, {**proposal, "reads": [read]},
                    {**proposal, "operations": []}]:
        with pytest.raises(ValidationError):
            validator.validate(invalid)
    validator.validate({**proposal, "action": "inspect", "reads": [read], "operations": []})
    validator.validate({**proposal, "action": "reply", "reads": [], "operations": []})


def test_model_proposal_compiles_against_exact_base_without_mutating_the_draft():
    db, source = seeded()
    before = project_state(db, "adaos-site")
    proposal = propose_chat_change(db, project_id="adaos-site", expected_source_digest=digest(source),
                                  run_id="a" * 40, actor_id="owner:one", decision=decision())
    assert proposal["change_set"]["validation"]["status"] == "passed"
    assert proposal["change_set"]["origin"]["channel"] == "chat"
    assert project_state(db, "adaos-site") == before
    assert "Changed through the customer chat" in json.dumps(proposal)
    with pytest.raises(ValueError, match="revision_conflict"):
        propose_chat_change(db, project_id="adaos-site", expected_source_digest="sha256:" + "0" * 64,
                            run_id="b" * 40, actor_id="owner:one", decision=decision())


def test_model_cannot_add_executable_content_or_edit_another_project():
    db, source = seeded()
    for target, value in [("resources.hero.data.script", "alert(1)"),
                          ("resources.other-project.data.title", "Escaped")]:
        with pytest.raises(ValueError):
            propose_chat_change(db, project_id="adaos-site", expected_source_digest=digest(source),
                                run_id="c" * 40, actor_id="owner:one", decision=decision(target, value))


def test_object_edit_expands_into_canonical_fields_and_preserves_omitted_content():
    from adaos.services.site_studio import apply_change_set
    from adaos.services.site_studio.chat_authoring import parse_chat_operations
    db, source = seeded()
    update = decision('resources.hero.data', {'title': 'New title', 'body': 'New body'})
    operations = parse_chat_operations(update)
    assert [op['target'] for op in operations] == ['resources.hero.data.title', 'resources.hero.data.body']
    assert len({op['operation_id'] for op in operations}) == 2
    result = propose_chat_change(db, project_id='adaos-site', expected_source_digest=digest(source),
        run_id='object-edit', actor_id='owner:one', decision=update)
    assert result['change_set']['operations'] == operations
    assert project_state(db, 'adaos-site')['active_source_digest'] == digest(source)
    changed = apply_change_set(source, result['change_set'])
    for field in ('statement', 'actions'):
        assert changed['resources'][1]['data'][field] == source['resources'][1]['data'][field]
    assert changed['resources'][1]['data']['title'] == 'New title'
    with pytest.raises(ValueError):
        propose_chat_change(db, project_id='adaos-site', expected_source_digest=digest(source),
            run_id='forbidden-object', actor_id='owner:one', decision=decision('resources.hero.data', {'script': 'run()'}))


def test_known_theme_name_compiles_as_the_same_canonical_operation():
    from adaos.services.site_studio.chat_authoring import parse_chat_operations
    db, source = seeded()
    update = decision('font.family', 'light-sans')
    update['operations'][0]['kind'] = 'set_theme_token'
    expected = parse_chat_operations(update)
    assert expected[0]['target'] == 'theme.tokens.font.family'
    result = propose_chat_change(db, project_id='adaos-site', expected_source_digest=digest(source),
        run_id='theme-edit', actor_id='owner:one', decision=update)
    assert result['change_set']['operations'] == expected
    update['operations'][0].update(target='unknown.style', value_json='"run()"')
    with pytest.raises(ValueError):
        propose_chat_change(db, project_id='adaos-site', expected_source_digest=digest(source),
            run_id='unknown-theme', actor_id='owner:one', decision=update)


def test_structural_short_names_are_normalized_to_canonical_owned_namespaces():
    from adaos.services.site_studio.chat_authoring import parse_chat_operations
    update = decision('new-people', {'id': 'new-people'})
    update['operations'][0]['kind'] = 'add_resource'
    assert parse_chat_operations(update)[0]['target'] == 'resources.new-people'
    update['operations'][0].update(kind='add_route', target='details')
    assert parse_chat_operations(update)[0]['target'] == 'routes.details'


def test_context_supplies_contracts_and_bounded_inspection_without_client_source():
    _, source = seeded()
    packet = build_chat_context(source, project_id="adaos-site", source_digest=digest(source),
                               target={"goal": "Build a site"}, user_text="Change the hero", selected_anchor="routes.landing.sections.hero")
    assert packet["selection"]["schema"]
    assert packet["selection"]["resource"]["id"] == "hero"
    assert packet["outline"][0]["sections"][1]["source_anchor"] == "routes.landing.sections.hero"
    assert json.loads(read_chat_source(source, "resources.hero.data.body")["text"])
    with pytest.raises(ValueError):
        read_chat_source(source, "../.env")


def test_partial_collection_keeps_real_indexes_and_reads_are_separate_from_source():
    from adaos.services.site_studio import apply_change_set
    from adaos.services.site_studio.chat_authoring import parse_chat_operations
    _, source = seeded()
    people = [{"title": f"Person {i}", "body": "Role", "monogram": str(i)} for i in range(13)]
    source['resources'].append({'id': 'board', 'type': 'site.card_collection', 'public': True,
                                'data': {'title': 'Board', 'items': people}})
    source['routes'][0]['sections'].append({'id': 'board', 'component': 'site.cardGrid', 'resource_ref': 'board'})
    packet = build_chat_context(source, project_id='adaos-site', source_digest=digest(source), target={},
                                user_text='Change person 8', selected_anchor='routes.landing.sections.board')
    summary = next(r for r in packet['resources'] if r['id'] == 'board')
    assert isinstance(summary['data']['items'], list)
    assert summary['data']['items'][8] == people[8]
    assert isinstance(packet['selection']['resource']['data']['items'], list)
    omitted = next(o for o in packet['source_omissions'] if o['target'] == 'resources.board.data.items')
    assert (omitted['shown'], omitted['total']) == (12, 13)
    full = read_chat_source(source, omitted['read']['target'])
    assert len(json.loads(full['text'])) == 13
    change = _change_set(source, value='unused')
    change['operations'] = parse_chat_operations(decision('resources.board.data.items.8.body', 'Updated'))
    changed = apply_change_set(source, change)
    result = next(r for r in changed['resources'] if r['id'] == 'board')['data']['items']
    assert len(result) == 13 and result[8]['body'] == 'Updated'
    assert result[:8] == people[:8] and result[9:] == people[9:]
    assert people[8]['body'] == 'Role'


def test_followup_keeps_full_user_request_and_excludes_progress_messages():
    _, source = seeded()
    original = 'Please change the council. ' + 'Details. ' * 70 + 'https://example.org/portrait.jpg'
    recent = [{'id': 'request', 'from': 'user', 'text': original}]
    for i in range(8):
        recent.extend([{'id': f'job-{i}-phase-0-inspect', 'from': 'assistant', 'text': 'Reading', 'progress_phase': 'inspect'},
                       {'id': f'job-{i}-response', 'from': 'system', 'text': 'Retry later', 'progress_phase': 'review'}])
    packet = build_chat_context(source, project_id='adaos-site', source_digest=digest(source), target={},
                                user_text='Try now', recent=recent)
    assert packet['recent'][0] == {'from': 'user', 'text': original}
    assert len(packet['recent']) == 9 and all(m['text'] != 'Reading' for m in packet['recent'])


def test_section_resource_alias_is_scoped_and_unambiguous():
    db, source = seeded()
    section = source['routes'][0]['sections'][1]
    resource_id = section['resource_ref']
    # Existing fixtures normally share both IDs. Give only the section a distinct ID.
    section['id'] = 'visible-opening'
    read = read_chat_source(source, 'resources.visible-opening.data.title')
    assert read['target'] == f'resources.{resource_id}.data.title'
    assert json.loads(read['text']) == source['resources'][1]['data']['title']
    duplicate = deepcopy(source['routes'][0])
    duplicate['id'] = 'another-route'
    duplicate['sections'][1]['resource_ref'] = source['resources'][0]['id']
    source['routes'].append(duplicate)
    with pytest.raises(ValueError, match='multiple resources'):
        read_chat_source(source, 'resources.visible-opening.data.title')
    with pytest.raises(ValueError):
        read_chat_source(source, 'resources.another-project.data.title')


def test_private_source_references_expose_the_exact_distinct_inspection_command():
    _, source = seeded()
    material = {"id": "source-original", "kind": "source", "label": "old-site.html",
                "uri": "adaos-material://adaos-site/source/source-original", "digest": "sha256:" + "a" * 64}
    packet = build_chat_context(source, project_id="adaos-site", source_digest=digest(source),
                               target={}, user_text="Read the original", materials=[material])
    assert packet["materials"][0]["read"] == {"kind": "material", "target": "source-original", "offset": 0}
    assert "read" not in material
    with pytest.raises(ValueError):
        read_chat_source(source, "source-original")


def test_source_files_are_private_versioned_and_scoped_with_metadata_cas():
    db, _ = seeded()
    original = b"<html>Original reference, never exported</html>"
    item = save_source_file(db, project_id="adaos-site", title="prototype.html", folder="originals",
                            content=original, media_type="text/html")
    assert item["uri"].startswith("adaos-material://adaos-site/")
    read = read_source_file(db, project_id="adaos-site", identity=item["id"], expected_revision=1)
    assert read["content"].encode() == original
    assert not source_files(db, "other-site")
    with pytest.raises(ValueError, match="not available"):
        read_source_file(db, project_id="other-site", identity=item["id"], expected_revision=1)
    renamed = save_source_file(db, project_id="adaos-site", identity=item["id"], expected_revision=1,
                               title="old-site.html", folder="originals", note="Use layout only")
    assert renamed["revision"] == 2 and renamed["digest"] == item["digest"]
    with pytest.raises(ValueError, match="revision_conflict"):
        delete_source_file(db, project_id="adaos-site", identity=item["id"], expected_revision=1)
    delete_source_file(db, project_id="adaos-site", identity=item["id"], expected_revision=2)
    assert not source_files(db, "adaos-site")
    with pytest.raises(ValueError, match="not available"):
        read_source_file(db, project_id="adaos-site", identity=item["id"], expected_revision=2)
    assert original not in json.dumps(project_state(db, "adaos-site")).encode()


@pytest.mark.parametrize("title,folder", [("../file", ""), ("file", "../private"), ("a\\b", ""), ("file", "C:/private")])
def test_material_paths_cannot_name_host_files(title, folder):
    db, _ = seeded()
    with pytest.raises(ValueError):
        save_source_file(db, project_id="adaos-site", title=title, folder=folder, content=b"reference")


def test_replacement_keeps_original_bytes_and_invalidates_the_selected_reference():
    db, _ = seeded()
    item = save_source_file(db, project_id="adaos-site", title="facts.txt", content=b"first")
    newer = save_source_file(db, project_id="adaos-site", identity=item["id"], expected_revision=1,
                             title="facts.txt", content=b"second")
    assert newer["digest"] != item["digest"]
    with pytest.raises(ValueError, match="revision_conflict"):
        read_source_file(db, project_id="adaos-site", identity=item["id"], expected_revision=1)
    with pytest.raises(ValueError, match="replaced"):
        read_source_file(db, project_id="adaos-site", identity=item["id"], expected_revision=2, expected_digest=item["digest"])
    assert db.execute("SELECT count(*) FROM site_studio_material_objects").fetchone()[0] == 2


def test_nested_property_form_uses_typed_paths_and_retains_the_rest_of_a_collection():
    from adaos.services.site_studio import apply_change_set, compile_site_revision
    _, source = seeded()
    source["resources"][1]["data"]["facts"] = [{"name": "First", "enabled": True}, {"name": "Second", "enabled": False}]
    editor = component_editor(source, "hero", revision=3, node_path="facts.0")
    name = next(key for key, path in editor["field_paths"].items() if path == "facts.0.name")
    enabled = next(key for key, path in editor["field_paths"].items() if path == "facts.0.enabled")
    assert next(field for field in editor["fields"] if field["id"] == enabled)["type"] == "toggle"
    operations = component_field_operations(editor, {name: "Updated", enabled: True})
    assert operations == [{"operation_id": "property-1", "kind": "set_resource_field",
                           "target": "resources.hero.data.facts.0.name", "value": "Updated"}]
    change = _change_set(source, value="unused")
    change["operations"] = operations
    changed = apply_change_set(source, change)
    assert changed["resources"][1]["data"]["facts"] == [{"name": "Updated", "enabled": True}, {"name": "Second", "enabled": False}]
    assert compile_site_revision(changed)["preview"]["source"]["resources"] == changed["resources"]
    assert source["resources"][1]["data"]["facts"][1]["name"] == "Second"
    with pytest.raises(ValueError, match="displayed"):
        component_field_operations(editor, {"unexposed": "write"})
    with pytest.raises(ValueError, match="type"):
        component_field_operations(editor, {enabled: "true"})


@pytest.mark.parametrize("index", ["-1", "2", "01", "name"])
def test_array_paths_reject_aliases_and_missing_elements_without_mutating_source(index):
    from adaos.services.site_studio import apply_change_set
    from adaos.services.site_studio.contracts import SiteStudioContractError
    _, source = seeded()
    source["resources"][1]["data"]["facts"] = [{"name": "First"}, {"name": "Second"}]
    before = deepcopy(source)
    change = _change_set(source, value="unused")
    change["operations"][0].update(target=f"resources.hero.data.facts.{index}.name", value="Invalid")
    with pytest.raises(SiteStudioContractError, match="existing array index"):
        apply_change_set(source, change)
    assert source == before


def test_array_scalar_replacement_and_nested_arrays_keep_their_shape():
    from adaos.services.site_studio import apply_change_set
    _, source = seeded()
    source["resources"][1]["data"]["matrix"] = [["first", "second"], ["third"]]
    change = _change_set(source, value="unused")
    change["operations"][0].update(target="resources.hero.data.matrix.0.1", value="Updated")
    assert apply_change_set(source, change)["resources"][1]["data"]["matrix"] == [["first", "Updated"], ["third"]]
