from __future__ import annotations

import json

import pytest

from adaos.e2e import builder_lifecycle as steps
from adaos.e2e.builder import BuilderE2EUnavailable


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "dev")
    return {"run_id": "run", "case_id": "case", "repetition": 1, "step_id": "implement",
            "bundle_dir": str(tmp_path), "webspace_id": "e2e-run-case-1", "retain_test_projects": True,
            "owned_artifacts": [{"draft_id": "draft", "project_id": "example", "primary_ref": "scenario:example"}]}


def test_lifecycle_refuses_non_dev_non_owned_and_unretained_targets(context, monkeypatch):
    for inputs, ctx in (({"object_id": "other"}, context), ({"object_id": "example"}, {**context, "retain_test_projects": False}),
                        ({"object_id": "example", "project_ref": "project:other"}, context)):
        with pytest.raises(ValueError):
            steps.execute("builder.workflow", inputs, ctx)
    monkeypatch.setenv("ENV_TYPE", "production")
    with pytest.raises(ValueError, match="ENV_TYPE"):
        steps.execute("builder.workflow", {"object_id": "example"}, context)


def test_review_requires_exact_artifact_and_existing_scoped_evidence(context, tmp_path):
    expected = {"stage": "prototype", "webui_digest": "sha256:expected"}
    with pytest.raises(BuilderE2EUnavailable):
        steps._review({"review_file": "review.json"}, context, expected=expected)
    with pytest.raises(ValueError, match="inside"):
        steps._review({"review_file": "../review.json"}, context, expected=expected)
    path = tmp_path / "review.json"
    path.write_text(json.dumps({**expected, "webui_digest": "stale"}), encoding="utf-8")
    with pytest.raises(ValueError, match="exact"):
        steps._review({"review_file": "review.json"}, context, expected=expected)
    review = {**expected, "behavior_checks": [{"id": "render.ready", "status": "passed", "evidence_refs": ["probe.json"]}]}
    path.write_text(json.dumps(review), encoding="utf-8")
    with pytest.raises(ValueError, match="existing evidence"):
        steps._review({"review_file": "review.json"}, context, expected=expected)
    (tmp_path / "probe.json").write_text("{}", encoding="utf-8")
    assert steps._review({"review_file": "review.json"}, context, expected=expected) == (review, "review.json")


def test_automation_uses_exact_accepted_prototype_explicit_brief_and_isolated_webspace(context, monkeypatch):
    seen = []
    monkeypatch.setattr(steps.workflow, "require_current_prototype_acceptance", lambda *args: seen.append(args) or {})
    monkeypatch.setattr(steps.automation, "get_state", lambda **kwargs: {})
    monkeypatch.setattr(steps.automation, "start", lambda **kwargs: {"ok": True, "session": {"session_id": "session"}, "kwargs": kwargs})
    with pytest.raises(ValueError, match="explicit implementation brief"):
        steps.execute("automation.start", {"object_id": "example"}, context)
    inputs = {"object_id": "example", "implementation_brief": "Persist records and reject stale writes."}
    result = steps.execute("automation.start", inputs, context)
    assert seen == [("scenario", "example")]
    assert result["kwargs"]["webspace_id"] == context["webspace_id"]
    assert result["kwargs"]["implementation_brief"] == inputs["implementation_brief"]
    existing = {"ok": True, "session": {"session_id": "session", "conversation_id": result["kwargs"]["conversation_id"], "status": "completed"}}
    monkeypatch.setattr(steps.automation, "get_state", lambda **kwargs: existing)
    monkeypatch.setattr(steps.automation, "start", lambda **kwargs: pytest.fail("resume must not launch another worker"))
    assert steps.execute("automation.start", inputs, context)["duplicate"] is True
    with pytest.raises(ValueError, match="resume inputs"):
        steps.execute("automation.start", {**inputs, "implementation_brief": "Changed"}, context)


@pytest.mark.parametrize("status,ok", [("completed", True), ("failed", False), ("awaiting_input", False)])
def test_wait_reports_exact_terminal_session_not_queued_ack(context, monkeypatch, status, ok):
    monkeypatch.setattr(steps.automation, "get_state", lambda **kwargs: {"session": {"session_id": "s1", "status": status}})
    result = steps.execute("automation.wait", {"object_id": "example", "session_id": "s1"}, context)
    assert result["ok"] is ok
    assert result["status"] == status
    with pytest.raises(ValueError, match="exact started session"):
        steps.execute("automation.wait", {"object_id": "example", "session_id": "other"}, context)


def test_release_uses_current_candidate_and_explicit_confirmation(context, monkeypatch):
    monkeypatch.setattr(steps.workflow, "get_state", lambda *args: {"delivery": {"candidate_id": "beta", "package_digest": "digest"}})
    monkeypatch.setattr(steps.lifecycle, "publish_candidate", lambda *args, **kwargs: {"ok": True, "target": args, "identity": kwargs})
    with pytest.raises(ValueError, match="exact current"):
        steps.execute("release.promote", {"object_id": "example", "candidate_id": "other", "confirmed": True}, context)
    with pytest.raises(ValueError, match="confirmed"):
        steps.execute("release.promote", {"object_id": "example", "candidate_id": "beta"}, context)
    result = steps.execute("release.promote", {"object_id": "example", "candidate_id": "beta", "confirmed": True}, context)
    assert result["target"] == ("scenario", "example")
    assert result["identity"]["idempotency_key"] == "run:case:1:implement"


def test_retained_automation_is_not_mislabeled_as_an_unapproved_prototype():
    assert steps.retained_stage([]) == {"stage": "prototype", "acceptance": "not_approved"}
    history = [{"type": kind, "status": "passed"} for kind in ("prototype.accept", "automation.start", "trial.prepare")]
    assert steps.retained_stage(history) == {"stage": "trial", "acceptance": "accepted"}
    assert steps.retained_stage([*history, {"type": "release.promote", "status": "failed"}])["stage"] == "trial"
