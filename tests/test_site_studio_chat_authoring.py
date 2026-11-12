from copy import deepcopy
import json
import sqlite3

import pytest

from adaos.services.site_studio import commit_revision, digest, project_state
from adaos.services.site_studio.chat_authoring import build_chat_context, propose_chat_change, read_chat_source
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
    _, source = seeded()
    source["resources"][1]["data"]["facts"] = [{"name": "First", "enabled": True}, {"name": "Second", "enabled": False}]
    editor = component_editor(source, "hero", revision=3, node_path="facts.0")
    name = next(key for key, path in editor["field_paths"].items() if path == "facts.0.name")
    enabled = next(key for key, path in editor["field_paths"].items() if path == "facts.0.enabled")
    assert next(field for field in editor["fields"] if field["id"] == enabled)["type"] == "toggle"
    operations = component_field_operations(editor, {name: "Updated", enabled: True})
    assert operations == [{"operation_id": "property-1", "kind": "set_resource_field",
                           "target": "resources.hero.data.facts.0.name", "value": "Updated"}]
    assert source["resources"][1]["data"]["facts"][1]["name"] == "Second"
    with pytest.raises(ValueError, match="displayed"):
        component_field_operations(editor, {"unexposed": "write"})
    with pytest.raises(ValueError, match="type"):
        component_field_operations(editor, {enabled: "true"})
