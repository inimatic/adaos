import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from adaos.services.builder.retained_resource_handoff import read_retained_handoff, select_retained_handoff
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker


@pytest.fixture
def retained(tmp_path):
    target = {"type": "scenario", "id": "sample"}
    acceptance = {"acceptance_id": "approved", "digest": "sha256:accepted", "change_id": "change",
        "revision": "006", "prototype_resources": [{"resource_type": "prototype.rows", "records_digest": "sha256:records"}]}
    bundle = {"schema": "adaos.resource.local_crud.v1", "owner_ref": "skill:sample_skill", "seed_policy": "if_missing", "seed": [],
        "resource_definition": {"schema": "adaos.resource.definition.v1", "resource_type": "skill.sample_skill.rows",
            "version": "1.0.0", "title": "Rows", "authority": {"provider": "local_crud", "binding": "sample_skill"},
            "record_schema_ref": "inline:rows", "record_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            "query": {}, "operations": [{"id": "list", "kind": "list"}], "views": [], "events": {}, "i18n": {}, "access": {}, "privacy": {},
            "metadata": {"prototype_records_digest": "sha256:records"}}}
    handoff = {"project_ref": "scenario:sample", "acceptance_id": "approved", "change_id": "change", "revision": "006",
        "companion_skill_id": "sample_skill", "resources": [{"source_resource_type": "prototype.rows", "bundle": bundle}]}
    root = tmp_path / "task.first/input"
    root.mkdir(parents=True)
    def write(name, value):
        path = root / name
        path.parent.mkdir(exist_ok=True)
        raw = json.dumps(value, ensure_ascii=False).encode()
        path.write_bytes(raw)
        return {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    assignment = {"task_id": "task.first", "target": target, "realize_request": {
        "links": {"automation_session_id": "session", "iteration": 0},
        "artifacts": {"prototype_acceptance": acceptance, "companion_skill_ids": ["sample_skill"]}}}
    write("assignment.json", assignment)
    handoff_file = write("prototype-resource-handoff.json", handoff)
    files = [handoff_file, write("packet.json", {"task_id": "task.first", "prototype_resource_handoff": handoff})]
    prompt = ("## Read-only task inputs\n\n```json\n" + json.dumps(files) + "\n```\n").encode()
    write("model-attempts/001.prompt.json", {"task_id": "task.first", "prompt_bytes": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt).hexdigest()})
    (root / "model-attempts/001.prompt.md").write_bytes(prompt)
    return SimpleNamespace(root=root, runs=tmp_path, acceptance=acceptance, target=target, handoff=handoff,
        reference={"source_task_id": "task.first", "handoff_sha256": handoff_file["sha256"]}, write=write)


def read(fixture, **overrides):
    args = dict(acceptance=fixture.acceptance, target=fixture.target,
        companion_skill_ids=["sample_skill"], session_id="session", iteration=1)
    args.update(overrides)
    return read_retained_handoff(fixture.runs, fixture.reference, **args)


def test_exact_handoff_survives_later_mutable_preview_without_loading_live_resources(retained, monkeypatch):
    assert read(retained) == retained.handoff
    worker = SimpleNamespace(runs_root=retained.runs,
        _companion_skill_ids=lambda _: ["sample_skill"],
        _prototype_resource_completion=lambda *args, **kwargs: {"model_required": True})
    assignment = {"target": retained.target, "realize_request": {"links": {"automation_session_id": "session", "iteration": 1,
        "prototype_resource_handoff_reference": retained.reference}, "artifacts": {"prototype_acceptance": retained.acceptance,
        "context_projection": {"artifacts": {"prototype": {"acceptance": retained.acceptance}}}}}}
    result = LocalSkillFactoryWorker._prototype_resource_handoff_from_assignment(worker, assignment, retained.runs / "new")
    assert result["resources"] == retained.handoff["resources"]
    assert result["mode"] == "implementation_blueprint"


@pytest.mark.parametrize("overrides", [{"target": {"type": "scenario", "id": "other"}},
    {"session_id": "other"}, {"iteration": 0}, {"companion_skill_ids": ["other_skill"]}])
def test_no_cross_application_session_or_backward_iteration_reuse(retained, overrides):
    with pytest.raises(ValueError, match="lineage"):
        read(retained, **overrides)


@pytest.mark.parametrize("field", ["digest", "revision", "change_id", "prototype_resources"])
def test_changed_acceptance_needs_new_handoff(retained, field):
    changed = copy.deepcopy(retained.acceptance)
    changed[field] = [] if field == "prototype_resources" else "changed"
    with pytest.raises(ValueError, match="lineage"):
        read(retained, acceptance=changed)


@pytest.mark.parametrize("name", ["packet.json", "prototype-resource-handoff.json", "model-attempts/001.prompt.md", "model-attempts/001.prompt.json"])
def test_mutated_retained_inputs_fail_closed(retained, name):
    path = retained.root / name
    path.write_bytes(path.read_bytes() + b" ")
    if name.endswith(".prompt.json"):
        retained.write(name, {"task_id": "other"})
    with pytest.raises(ValueError):
        read(retained)


def test_selection_uses_only_explicit_bounded_history_and_same_acceptance(retained):
    session = {"iteration": 2, "session_id": "session", "prototype_acceptance": retained.acceptance,
        "task_history": ["../escape", "task.first", "task.failed-before-input"]}
    args = dict(target=retained.target, companion_skill_ids=["sample_skill"])
    assert select_retained_handoff(retained.runs, session, **args) == retained.reference
    assert select_retained_handoff(retained.runs, {**session, "iteration": 0}, **args) is None
    assert select_retained_handoff(retained.runs, {**session, "task_history": []}, **args) is None
    session["prototype_acceptance"] = {**retained.acceptance, "digest": "changed"}
    assert select_retained_handoff(retained.runs, session, **args) is None


def test_retained_task_path_cannot_escape(retained):
    retained.reference["source_task_id"] = "../outside"
    with pytest.raises(ValueError, match="task identity"):
        read(retained)
