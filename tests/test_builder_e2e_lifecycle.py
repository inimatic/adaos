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


def test_correction_is_explicit_owned_terminal_and_not_automatically_replayed(context, monkeypatch):
    session = {"session_id": "s", "iteration": 3, "status": "failed", "conversation_id": "c"}
    monkeypatch.setattr(steps.automation, "get_state", lambda **kwargs: {"session": session})
    monkeypatch.setattr(steps.automation, "submit", lambda text, **kwargs: {"ok": True, "kwargs": kwargs})
    monkeypatch.setattr(steps.workflow, "accept_prototype", lambda *a, **kw: pytest.fail("not a Prototype acceptance"))
    inputs = {"object_id": "example", "text": "Move package-independent checks to their owning component.",
              "expected_session_id": "s", "expected_iteration": 3}
    for patch in ({"expected_iteration": 2}, {"expected_session_id": "other"}, {"text": ""}):
        with pytest.raises(ValueError):
            steps.execute("automation.submit", {**inputs, **patch}, context)
    session["status"] = "in_progress"
    with pytest.raises(ValueError, match="terminal"):
        steps.execute("automation.submit", inputs, context)
    session["status"] = "failed"
    result = steps.execute("automation.submit", inputs, context)
    assert result["review_interventions"] == 1
    assert result["kwargs"]["expected_iteration"] == 3
    assert result["kwargs"]["webspace_id"] == context["webspace_id"]
    with pytest.raises(FileExistsError):
        steps.execute("automation.submit", inputs, context)


def test_browser_requires_owned_validated_unapproved_preview(context):
    from pathlib import Path
    from adaos.e2e import builder_browser

    with pytest.raises(ValueError, match="validated test preview"):
        builder_browser.execute({"object_id": "example"}, context, repo_root=Path.cwd())
    outputs = {"create": {"scenario_id": "example", "draft_id": "draft"},
               "validate": {"review_preview": {"scenario_id": "example", "stage": "prototype", "test": True}},
               "approve": {"acceptance": {"decision": "accepted"}}}
    with pytest.raises(ValueError, match="accepted prototypes"):
        builder_browser.execute({"object_id": "example"}, {**context, "outputs": outputs}, repo_root=Path.cwd())


def test_lifecycle_suite_keeps_automation_brief_out_of_prototype_requests():
    from pathlib import Path
    from adaos.e2e.builder import load_builder_e2e_suite

    loaded = load_builder_e2e_suite(Path("e2e/builder/development/lifecycle/suite.yaml"))
    case = loaded.cases[0]
    declared = {step["id"]: step for step in case["steps"]}
    assert loaded.suite["defaults"]["retain_test_projects"] is True
    assert declared["implement"]["type"] == "automation.start"
    assert "implementation_brief" not in declared["design"]["input"]
    assert declared["approve"]["type"] == "prototype.accept"
    assert declared["implementation-result"]["expect"]["values"]["status"] == "completed"
    assert declared["trial"]["type"] == "trial.prepare"


@pytest.mark.parametrize("timeout", [False, True])
def test_browser_records_scoped_evidence_and_clears_inherited_probe_options(context, tmp_path, monkeypatch, timeout):
    from pathlib import Path
    from types import SimpleNamespace
    from adaos.apps.cli import active_control
    from adaos.services import agent_context
    from adaos.e2e import builder_browser

    monkeypatch.setattr(active_control, "resolve_control_token", lambda **kwargs: "secret")
    monkeypatch.setattr(agent_context, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(subnet_id_value="subnet")))
    monkeypatch.setenv("ADAOS_E2E_DICTIONARY_PROBE", "1")
    monkeypatch.setenv("ADAOS_E2E_SELECT_WIDGET", "unrelated")
    outputs = {"create": {"scenario_id": "example", "draft_id": "draft"},
               "validate": {"review_preview": {"scenario_id": "example", "stage": "prototype", "test": True,
                                               "webspace_id": "preview-owned"}}}
    ctx = {**context, "outputs": outputs, "locale": "ru"}
    seen = []

    def run(args, **kwargs):
        env = kwargs["env"]
        assert "ADAOS_E2E_DICTIONARY_PROBE" not in env
        assert "ADAOS_E2E_SELECT_WIDGET" not in env
        assert env["ADAOS_E2E_WEBSPACE_ID"] == "preview-owned"
        assert env["ADAOS_E2E_LOCALE"] == "ru"
        seen.append(Path(env["ADAOS_E2E_OUTPUT"]))
        if timeout:
            raise builder_browser.subprocess.TimeoutExpired(args, 240, output="Partial output".encode("utf-8"))
        (seen[-1] / "review.json").write_text("{}", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="review completed", stderr="")

    monkeypatch.setattr(builder_browser.subprocess, "run", run)
    for _ in range(2):
        result = builder_browser.execute({"object_id": "example"}, ctx, repo_root=Path.cwd())
        assert result["ok"] is not timeout
        assert (tmp_path / result["evidence_ref"]).is_file()
        assert "secret" not in (seen[-1] / "input.json").read_text(encoding="utf-8")
    assert seen[0] != seen[1]
    if timeout:
        assert "Partial output" in (seen[0] / "probe.log").read_text(encoding="utf-8")
