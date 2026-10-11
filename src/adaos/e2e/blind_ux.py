"""Validate and unblind screenshot judgments without making model requests."""
from collections import defaultdict
from statistics import mean


def paired_samples(samples: list[dict], arms: tuple[str, str]) -> list[tuple[dict, dict]]:
    """Pair every chronological repetition; never overwrite or cherry-pick arms."""
    by_arm = {arm: sorted((s for s in samples if s["arm"] == arm),
                          key=lambda s: s["order"]) for arm in arms}
    if len(by_arm[arms[0]]) != len(by_arm[arms[1]]):
        raise ValueError("Matched arms require the same number of repetitions")
    return list(zip(by_arm[arms[0]], by_arm[arms[1]]))


def validate_judgment(value: dict, packet: dict) -> dict:
    pairs = value.get("pairs")
    expected = {item["id"] for item in packet["unblinding"]}
    if not isinstance(pairs, list) or len(pairs) != len(expected):
        raise ValueError("Judge pair count differs from the frozen packet")
    seen = set()
    for pair in pairs:
        if pair.get("id") not in expected or pair["id"] in seen:
            raise ValueError("Unknown or duplicate judge pair identity")
        seen.add(pair["id"])
        if pair.get("winner") not in ("A", "B", "tie") or not isinstance(pair.get("reason"), str):
            raise ValueError("Invalid judge decision")
        for key in ("A_scores", "B_scores"):
            scores = pair.get(key)
            if not isinstance(scores, list) or len(scores) != 4 or any(
                type(score) is not int or not 0 <= score <= 4 for score in scores
            ):
                raise ValueError("Exactly four integer rubric scores 0..4 required")
    return value


def aggregate_judgments(records: list[dict]) -> dict:
    groups = defaultdict(list)
    for record in records:
        packet, judgment = record["packet"], record["judgment"]
        validate_judgment(judgment, packet)
        mapping = {item["id"]: item for item in packet["unblinding"]}
        for pair in judgment["pairs"]:
            identity = mapping[pair["id"]]
            winner = identity.get(pair["winner"], "tie")
            groups[identity["cohort"]].append({
                "packet": record["name"], "positions_reversed": packet["positions_reversed"],
                "winner": winner, "scores": {identity[label]: pair[label + "_scores"] for label in ("A", "B")},
                "reason": pair["reason"],
            })
    results = []
    for cohort, judgments in sorted(groups.items()):
        paired = len(judgments) == 2 and {j["positions_reversed"] for j in judgments} == {False, True}
        winners = {j["winner"] for j in judgments}
        arms = sorted({arm for judgment in judgments for arm in judgment["scores"]})
        if len(arms) != 2 or any(set(j["scores"]) != set(arms) for j in judgments):
            raise ValueError("Context arms drifted within a blinded pair")
        results.append({"cohort": cohort, "paired_positions_complete": paired,
            "position_consistent": len(winners) == 1 if paired else None,
            "winner": next(iter(winners)) if paired and len(winners) == 1 else "position_inconsistent" if paired else None,
            "mean_dimension_scores": {arm: [mean(j["scores"][arm][dimension] for j in judgments)
                for dimension in range(4)] for arm in arms}, "judgments": judgments})
    return {"schema": "adaos.e2e.blind_ux_result.v1", "packets": len(records), "pairs": results,
        "scope": "Visible screenshot model-judge proxy; NOT functional proof, human preference or human-calibrated UX",
        "dimension_order": ["hierarchy", "action_discovery", "wide_narrow_readability", "density_friction"]}
