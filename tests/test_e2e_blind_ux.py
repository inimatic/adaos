import pytest
from adaos.e2e.blind_ux import aggregate_judgments, paired_samples, validate_judgment


def record(reverse=False, winner="A"):
    return {"name": "BA" if reverse else "AB", "packet": {"positions_reversed": reverse,
        "unblinding": [{"id": "pair-1", "cohort": "cohort", "A": "task-aware" if reverse else "full-mcp",
            "B": "full-mcp" if reverse else "task-aware"}]}, "judgment": {"pairs": [{"id": "pair-1",
        "winner": winner, "A_scores": [1, 2, 3, 4], "B_scores": [4, 3, 2, 1], "reason": "Visible evidence"}]}}


def test_unblind_positions_before_aggregation():
    result = aggregate_judgments([record(), record(True, "B")])["pairs"][0]
    assert result["paired_positions_complete"] and result["position_consistent"]
    assert result["winner"] == "full-mcp"
    assert result["mean_dimension_scores"]["full-mcp"] == [2.5] * 4


def test_position_bias_is_not_a_win():
    result = aggregate_judgments([record(), record(True)])["pairs"][0]
    assert result["winner"] == "position_inconsistent"
    assert not result["position_consistent"]


@pytest.mark.parametrize("scores", [[1, 2, 3], [1, 2, 3, 5], [True, 2, 3, 4]])
def test_invalid_dimension_scores_rejected(scores):
    value = record()
    value["judgment"]["pairs"][0]["A_scores"] = scores
    with pytest.raises(ValueError):
        validate_judgment(value["judgment"], value["packet"])


def test_duplicate_or_missing_pairs_rejected():
    value = record()
    value["judgment"]["pairs"] *= 2
    with pytest.raises(ValueError):
        validate_judgment(value["judgment"], value["packet"])


def test_compact_arm_unblinding_preserves_both_repetitions():
    records = []
    for repetition in (1, 2):
        for reverse in (False, True):
            value = record(reverse, "B" if reverse else "A")
            mapping = value["packet"]["unblinding"][0]
            mapping["cohort"] = f"cohort:repeat-{repetition}"
            mapping["A"] = "task-aware-compact" if reverse else "full-mcp"
            mapping["B"] = "full-mcp" if reverse else "task-aware-compact"
            records.append(value)
    result = aggregate_judgments(records)
    assert len(result["pairs"]) == 2
    for pair in result["pairs"]:
        assert pair["paired_positions_complete"]
        assert pair["winner"] == "full-mcp"
        assert set(pair["mean_dimension_scores"]) == {"full-mcp", "task-aware-compact"}


def test_pair_samples_uses_chronological_repetitions_not_last_arm():
    samples = [{"order": i, "arm": arm} for i, arm in
               ((4, "full-mcp"), (2, "task-aware-compact"),
                (1, "full-mcp"), (3, "task-aware-compact"))]
    assert [[a["order"], b["order"]] for a, b in
            paired_samples(samples, ("full-mcp", "task-aware-compact"))] == [[1, 2], [4, 3]]
    with pytest.raises(ValueError, match="repetitions"):
        paired_samples(samples[:-1], ("full-mcp", "task-aware-compact"))
