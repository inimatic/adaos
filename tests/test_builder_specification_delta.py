import copy

import pytest

from adaos.services.builder.specification_delta import (
    apply_delta, digest, empty_specification, normalize_specification, prepare_delta,
)


def change(identifier="ch1"):
    return {"change_id": identifier, "issues": [{"issue_id": "i1"}], "source_message_ids": ["m1"]}


def operation(stage="prototype", **kwargs):
    return {"stage": stage, "operation": "add", "requirement_id": "record.editor",
            "text": "Edit a selected record", "acceptance_criteria": ["The editor opens"],
            "issue_ids": ["i1"], "source_message_ids": ["m1"], **kwargs}


def test_prototype_acceptance_does_not_claim_automation_and_merge_is_idempotent():
    spec = empty_specification()
    delta = prepare_delta({"operations": [operation(), operation("automation")]},
                          change=change(), specification=spec)
    prototype, applied = apply_delta(spec, delta, stage="prototype", evidence_ref="review:ui1")
    assert prototype["prototype"]["generation"] == 1
    assert prototype["automation"] == spec["automation"]
    assert prototype["prototype"]["requirements"]["record.editor"]["source_message_ids"] == ["m1"]
    assert apply_delta(prototype, applied, stage="prototype", evidence_ref="review:ui1") == (prototype, applied)
    implemented, final = apply_delta(prototype, applied, stage="automation", evidence_ref="checkpoint:1")
    assert implemented["automation"]["generation"] == 1
    assert final["applied"]["automation"]["evidence_ref"] == "checkpoint:1"
    assert spec == empty_specification()


def test_requirement_modify_remove_and_stale_base_require_review():
    original = empty_specification()
    first = prepare_delta({"operations": [operation()]}, change=change(), specification=original)
    spec, _ = apply_delta(original, first, stage="prototype", evidence_ref="review:1")
    next_delta = prepare_delta({"operations": [operation(operation="modify", text="Modified requirement")]},
                               change=change("ch2"), specification=spec)
    updated, _ = apply_delta(spec, next_delta, stage="prototype", evidence_ref="review:2")
    with pytest.raises(ValueError, match="base changed"):
        apply_delta(updated, next_delta, stage="prototype", evidence_ref="review:old")
    removal = prepare_delta({"operations": [operation(operation="remove", reason="User removed the editor")]},
                            change=change("ch3"), specification=updated)
    removed, _ = apply_delta(updated, removal, stage="prototype", evidence_ref="review:3")
    assert removed["prototype"]["requirements"] == {}
    assert removed["prototype"]["generation"] == 3


@pytest.mark.parametrize("kwargs,match", [
    ({"issue_ids": ["other"]}, "Issues"),
    ({"source_message_ids": ["other"]}, "source message"),
    ({"operation": "modify"}, "conflicts"),
    ({"operation": "invent"}, "Invalid"),
    ({"acceptance_criteria": []}, "acceptance criteria"),
    ({"stage": "stable"}, "Invalid"),
])
def test_invalid_delta_rejected_without_guessing(kwargs, match):
    with pytest.raises(ValueError, match=match):
        prepare_delta({"operations": [operation(**kwargs)]}, change=change(), specification=empty_specification())


def test_duplicate_operations_and_tampered_digests_rejected():
    spec = empty_specification()
    with pytest.raises(ValueError, match="Duplicate"):
        prepare_delta({"operations": [operation(), operation()]}, change=change(), specification=spec)
    delta = prepare_delta({"operations": [operation()]}, change=change(), specification=spec)
    tampered = copy.deepcopy(delta)
    tampered["operations"][0]["text"] = "Changed after admission"
    with pytest.raises(ValueError, match="digest mismatch"):
        apply_delta(spec, tampered, stage="prototype", evidence_ref="review:1")
    spec["prototype"]["digest"] = digest({"other": True})
    with pytest.raises(ValueError, match="digest mismatch"):
        normalize_specification(spec)
