from copy import deepcopy

import pytest

from adaos.sdk.builder.automation import plan_increment


@pytest.mark.parametrize("case,decision", [("unchanged", "unchanged"), ("absorbed", "already_absorbed"),
    ("disjoint", "compatible_delta"), ("conflict", "conflict"), ("contract", "contract_conflict")])
def test_bcn_planner_retains_current_authority_and_classifies_paid_work(case, decision):
    base = {"views": [{"id": "face", "title": "Original"}, {"id": "neck", "title": "Neck"}]}
    candidate = deepcopy(base)
    current = deepcopy(base)
    if case != "unchanged":
        candidate["views"][0]["title"] = "Retouch"
    if case == "absorbed":
        current = deepcopy(candidate)
    elif case == "disjoint":
        current["views"][1]["title"] = "Natural neck"
    elif case == "conflict":
        current["views"][0]["title"] = "Different accepted title"
    before = deepcopy((base, candidate, current))
    result = plan_increment(base=base, candidate=candidate, current=current, old_contract={"version": 1},
        new_contract={"version": 2 if case == "contract" else 1}, old_obligations=["preserve skin"], new_obligations=["preserve skin"])
    assert result["decision"] == decision
    assert (base, candidate, current) == before
    assert not result["apply_allowed"] and not result["model_started"]
    assert result["validation_required"]
    if case == "disjoint":
        assert result["changes"][0]["path"] == "/views/@face/title"
        assert result["next_action"] == "admit_bounded_replay"


def test_new_obligations_and_unstable_arrays_require_review_without_guessing():
    result = plan_increment(base={"columns": ["a", "b"]}, candidate={"columns": ["b", "a"]},
        current={"columns": ["a", "c"]}, old_obligations=["old"], new_obligations=["new"])
    assert result["decision"] == "conflict" and result["obligations_changed"]
    assert result["conflict_paths"] == ["/columns"] and not result["contract_checked"]


def test_missing_base_and_changed_json_type_never_count_as_unchanged():
    assert plan_increment(base=None, candidate={}, current={})["decision"] == "missing_evidence"
    result = plan_increment(base={"enabled": True}, candidate={"enabled": 1}, current={"enabled": False})
    assert result["decision"] == "conflict"
