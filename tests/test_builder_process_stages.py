from adaos.services.builder.process_stages import process_stages


def rows(**overrides):
    state = {"object_id": "sample", "prototype": {"head_revision": "003", "stable": True,
              "acceptance_required": True, "acceptance": {"revision": "003"}},
             "automation": {"source_prototype_revision": "003", "status": "completed", "head_task_id": "task-3"},
             "delivery": {"candidate_id": "candidate-3", "status": "prepared"},
             "workflow_description": {"state": "trial_review"}}
    state.update(overrides)
    return state


def test_current_step_is_independent_of_inspection():
    state = rows(interaction={"inspected_ref": "prototype:sample:003"})
    result = process_stages(state)
    assert [row["stage"] for row in result if row["current"]] == ["trial"]
    assert [row["stage"] for row in result if row["disabled"]] == ["stable"]


def test_older_revision_does_not_inherit_new_results():
    result = process_stages(rows(), revision="002")
    assert not any(row["current"] for row in result)
    assert [row["stage"] for row in result if not row["disabled"]] == ["change", "prototype"]
    assert not any(row["canOpenPlacement"] for row in result)


def test_unaccepted_revision_does_not_enable_automation():
    result = process_stages(rows(prototype={"head_revision": "003", "stable": False}, automation={}))
    assert next(row for row in result if row["stage"] == "automation")["disabled"] is True


def test_stale_acceptance_is_not_admission():
    result = process_stages(rows(prototype={"head_revision": "003", "stable": True, "acceptance_required": True,
                                           "acceptance": {"revision": "002"}}, automation={}))
    assert next(row for row in result if row["stage"] == "automation")["disabled"] is True
