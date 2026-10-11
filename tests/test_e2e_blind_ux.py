import pytest
from adaos.e2e.blind_ux import aggregate_judgments, validate_judgment


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
