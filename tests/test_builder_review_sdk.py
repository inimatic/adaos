from __future__ import annotations

from typing import Any

from adaos.sdk.builder import review


class _ReviewService:
    def __getattr__(self, name: str):
        def invoke(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"operation": name, "args": list(args), "kwargs": kwargs}

        return invoke


def test_review_sdk_exposes_full_durable_review_lifecycle(monkeypatch) -> None:
    service = _ReviewService()
    monkeypatch.setattr(review, "_service", lambda: service)

    assert review.submit({"review_id": "review.1"})["operation"] == "submit"
    assert review.context_for_next_request("scenario", "recipes")["operation"] == "context_for_next_request"
    assert review.withdraw("scenario", "recipes", "review.1", reason="wrong target")["operation"] == "withdraw"
    assert review.dismiss("scenario", "recipes", "review.1", reason="not applicable")["operation"] == "dismiss"
    assert review.accept_as_constraint(
        "scenario",
        "recipes",
        "review.1",
        kind="label_equals",
        expected="Recipe name",
        source_revision="001",
    )["operation"] == "accept_as_constraint"
    assert review.convert_to_issue(
        "scenario",
        "recipes",
        "review.1",
        issue={"issue_id": "issue.1", "title": "Fix label"},
    )["operation"] == "convert_to_issue"
    assert review.supersede(
        "scenario",
        "recipes",
        "review.1",
        reason="covered by a newer review",
        superseded_by_ref="review:review.2",
    )["operation"] == "supersede"
    assert review.resolve(
        "scenario",
        "recipes",
        "review.1",
        resolution_ref="revision:002",
    )["operation"] == "resolve"

