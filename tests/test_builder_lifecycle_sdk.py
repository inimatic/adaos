from __future__ import annotations

from adaos.sdk.builder import lifecycle


def _checkpoint_state() -> dict:
    return {
        "automation": {"status": "completed", "head_task_id": "task-1"},
        "delivery": {
            "status": "checkpoint",
            "checkpoint_change_id": "change-1",
            "package_digest": "sha256:" + "a" * 64,
            "source_revision": "b" * 40,
        },
        "change": {
            "change_id": "change-1",
            "member_change_ids": ["change-1"],
        },
    }


def test_prepare_trial_uses_one_waiting_then_result_transition(monkeypatch) -> None:
    transitions: list[tuple[str, dict]] = []
    monkeypatch.setattr(lifecycle.workflow, "get_state", lambda *_args: _checkpoint_state())

    def transition(_kind, _project, action, **kwargs):
        transitions.append((action, dict(kwargs.get("metadata") or {})))
        return {"workflow": {"governed": {"state": action}}}

    monkeypatch.setattr(lifecycle.workflow, "transition", transition)
    monkeypatch.setattr(
        lifecycle.projects,
        "prepare_candidate",
        lambda *_args, **_kwargs: {
            "ok": True,
            "candidate": {
                "candidate_id": "candidate-1",
                "release_digest": "sha256:" + "c" * 64,
                "package_digest": "sha256:" + "d" * 64,
                "base_release": "recipes@0.1.0",
                "base_release_digest": "sha256:" + "e" * 64,
            },
            "release": {
                "project_id": "recipes",
                "version": "0.2.0",
                "release_digest": "sha256:" + "c" * 64,
            },
            "trial_workspace": "trial://candidate-1",
        },
    )

    result = lifecycle.prepare_trial(
        "scenario",
        "recipes",
        actor="user:test",
        idempotency_key="trial-1",
    )

    assert [item[0] for item in transitions] == [
        "candidate_preparation_started",
        "candidate_prepared",
    ]
    assert transitions[0][1]["idempotency_key"] == "trial-1:start"
    assert transitions[1][1]["idempotency_key"] == "trial-1:success"
    assert transitions[1][1]["candidate_id"] == "candidate-1"
    assert result["workflow"]["governed"]["state"] == "candidate_prepared"
    assert result["resumed"] is False


def test_prepare_trial_resumes_observed_candidate_without_restarting(monkeypatch) -> None:
    state = _checkpoint_state()
    state["delivery"]["status"] = "activating"
    transitions: list[tuple[str, dict]] = []
    monkeypatch.setattr(lifecycle.workflow, "get_state", lambda *_args: state)

    def transition(_kind, _project, action, **kwargs):
        transitions.append((action, dict(kwargs.get("metadata") or {})))
        return {"workflow": {"governed": {"state": action}}}

    monkeypatch.setattr(lifecycle.workflow, "transition", transition)
    monkeypatch.setattr(
        lifecycle.projects,
        "prepare_candidate",
        lambda *_args, **_kwargs: {
            "ok": True,
            "candidate": {
                "candidate_id": "candidate-1",
                "release_digest": "sha256:" + "c" * 64,
                "package_digest": "sha256:" + "d" * 64,
            },
            "release": {"project_id": "recipes", "version": "0.2.0"},
        },
    )

    result = lifecycle.prepare_trial(
        "scenario",
        "recipes",
        actor="user:test",
        idempotency_key="trial-1",
    )

    assert [item[0] for item in transitions] == ["candidate_prepared"]
    assert transitions[0][1]["idempotency_key"] == "trial-1:success"
    assert result["resumed"] is True


def test_publish_candidate_passes_exact_apply_evidence_to_terminal_transition(
    monkeypatch,
) -> None:
    state = {
        "automation": {"status": "completed", "head_task_id": "task-1"},
        "delivery": {
            "status": "accepted",
            "candidate_id": "candidate-1",
            "package_digest": "sha256:" + "d" * 64,
        },
    }
    transitions: list[tuple[str, dict]] = []
    monkeypatch.setattr(lifecycle.workflow, "get_state", lambda *_args: state)

    def transition(_kind, _project, action, **kwargs):
        transitions.append((action, dict(kwargs.get("metadata") or {})))
        return {"workflow": {"governed": {"state": action}}}

    monkeypatch.setattr(lifecycle.workflow, "transition", transition)
    evidence = {
        "draft_ref": {"draft_id": "candidate:candidate-1", "revision": "a" * 40},
        "validation_evidence": [{"status": "passed"}],
        "approval": {
            "approval_id": "approval-1",
            "actor_id": "user:test",
            "policy_evidence": [{"approved": True}],
        },
        "activation": {
            "operation_id": "activation-1",
            "runtime_slot": "recipes",
            "health_receipt": {"status": "healthy"},
        },
        "rollback": {
            "mode": "workspace_lock_restore",
            "operation_ref": "activation-1",
        },
    }
    monkeypatch.setattr(
        lifecycle.projects,
        "promote_candidate",
        lambda *_args, **_kwargs: {
            "ok": True,
            "version": "0.2.0",
            "release": "recipes@0.2.0",
            "apply_evidence": evidence,
        },
    )

    result = lifecycle.publish_candidate(
        "scenario",
        "recipes",
        actor="user:test",
        idempotency_key="publish-1",
    )

    assert [item[0] for item in transitions] == ["publication_started", "publish"]
    assert transitions[0][1]["idempotency_key"] == "publish-1:start"
    assert transitions[1][1]["idempotency_key"] == "publish-1:success"
    assert transitions[1][1]["apply_evidence"] == evidence
    assert result["workflow"]["governed"]["state"] == "publish"


def test_publish_candidate_resumes_external_promotion_without_restarting(monkeypatch) -> None:
    state = {
        "automation": {"status": "completed", "head_task_id": "task-1"},
        "delivery": {
            "status": "accepted",
            "candidate_id": "candidate-1",
            "package_digest": "sha256:" + "d" * 64,
        },
        "publication": {"status": "publishing"},
    }
    transitions: list[tuple[str, dict]] = []
    monkeypatch.setattr(lifecycle.workflow, "get_state", lambda *_args: state)

    def transition(_kind, _project, action, **kwargs):
        transitions.append((action, dict(kwargs.get("metadata") or {})))
        return {"workflow": {"governed": {"state": action}}}

    monkeypatch.setattr(lifecycle.workflow, "transition", transition)
    monkeypatch.setattr(
        lifecycle.projects,
        "promote_candidate",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": "published",
            "version": "0.2.0",
            "release": "recipes@0.2.0",
            "apply_evidence": {"validation_evidence": [{"status": "passed"}]},
        },
    )

    result = lifecycle.publish_candidate(
        "scenario",
        "recipes",
        actor="user:test",
        idempotency_key="publish-1",
    )

    assert [item[0] for item in transitions] == ["publish"]
    assert transitions[0][1]["idempotency_key"] == "publish-1:success"
    assert result["workflow"]["governed"]["state"] == "publish"


def test_activity_dispatch_builds_implementation_brief_from_change(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        lifecycle.workflow,
        "get_state",
        lambda *_args: {
            "change": {
                "change_id": "change-1",
                "request": "Add a recipe search.",
                "issues": [
                    {
                        "acceptance_criteria": [
                            "Search is deterministic.",
                            "Empty results are explained.",
                        ]
                    }
                ],
            }
        },
    )

    def start(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "status": "queued"}

    monkeypatch.setattr(lifecycle.automation, "start", start)

    lifecycle.invoke_activity_command(
        "start_automation",
        "scenario",
        "recipes",
        actor="user:test",
        idempotency_key="automation-1",
        metadata={"webspace_id": "dev1"},
    )

    assert captured["change_set_id"] == "change-1"
    assert captured["webspace_id"] == "dev1"
    assert "Add a recipe search." in captured["implementation_brief"]
    assert "Search is deterministic." in captured["implementation_brief"]
