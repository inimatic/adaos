from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services.applications import (
    ApplicationDevelopmentCoordinator,
    ApplicationDevelopmentError,
    ApplicationDevelopmentOutcomeUnknown,
)


def test_development_coordinator_is_idempotent_and_durable(tmp_path: Path) -> None:
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    calls = []

    def execute():
        calls.append("called")
        return {"ok": True, "candidate_id": "candidate.1"}

    first = coordinator.execute(
        "create_trial", "app_test", actor_ref="user:owner",
        subnet_ref="subnet:home", capability="applications.develop",
        expected_revision=1, idempotency_key="trial-1",
        intent={"revision": "change.1"}, callback=execute,
    )
    repeated = coordinator.execute(
        "create_trial", "app_test", actor_ref="user:owner",
        subnet_ref="subnet:home", capability="applications.develop",
        expected_revision=1, idempotency_key="trial-1",
        intent={"revision": "change.1"}, callback=execute,
    )

    assert first == repeated
    assert first["status"] == "succeeded"
    assert calls == ["called"]
    assert coordinator.get(first["operation_id"])["result"]["candidate_id"] == "candidate.1"


def test_development_coordinator_blocks_blind_retry_after_unknown_outcome(tmp_path: Path) -> None:
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)

    with pytest.raises(TimeoutError):
        coordinator.execute(
            "publish_trial", "app_test", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.publish",
            expected_revision=1, idempotency_key="publish-1",
            intent={"candidate_id": "candidate.1"},
            callback=lambda: (_ for _ in ()).throw(TimeoutError("ack lost")),
        )
    with pytest.raises(ApplicationDevelopmentOutcomeUnknown):
        coordinator.execute(
            "publish_trial", "app_test", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.publish",
            expected_revision=1, idempotency_key="publish-1",
            intent={"candidate_id": "candidate.1"}, callback=lambda: {"ok": True},
        )

    operation = coordinator.list("app_test")[0]
    assert operation["status"] == "unknown"
    assert operation["recovery_reason"].startswith("outcome_unknown:TimeoutError")


def test_development_coordinator_requires_action_capability(tmp_path: Path) -> None:
    with pytest.raises(ApplicationDevelopmentError, match="applications.publish"):
        ApplicationDevelopmentCoordinator(tmp_path).execute(
            "promote_stable", "app_test", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.develop",
            expected_revision=1, idempotency_key="stable-1", intent={},
            callback=lambda: {"ok": True},
        )
