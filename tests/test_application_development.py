from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services.applications import (
    ApplicationDevelopmentCoordinator,
    ApplicationDevelopmentError,
    ApplicationDevelopmentOutcomeUnknown,
)


def test_development_summary_cache_drops_heavy_results_and_tracks_authority_changes(tmp_path, monkeypatch):
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    operation = coordinator.execute("create_trial", "app_test", actor_ref="user:owner", subnet_ref="subnet:home",
        capability="applications.develop", expected_revision=1, idempotency_key="trial-1",
        intent={"source_webspace_id": "dev-test"}, callback=lambda: {"evidence": ["large"] * 10000})
    first = coordinator.summaries()
    assert first[0]["intent"] == {"source_webspace_id": "dev-test"}
    assert "result" not in first[0]
    original = coordinator._read
    monkeypatch.setattr(coordinator, "_read", lambda p: pytest.fail("warm summary must not parse retained results"))
    assert coordinator.summaries() == first
    operation.update(status="unknown", updated_at="2026-11-06T00:00:00Z")
    atomic_write_json(coordinator._path(operation["operation_id"]), operation)
    monkeypatch.setattr(coordinator, "_read", original)
    assert coordinator.summaries()[0]["status"] == "unknown"
    assert coordinator.get(operation["operation_id"])["result"]["evidence"] == ["large"] * 10000


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


def test_development_coordinator_rejects_cross_application_idempotency_reuse(
    tmp_path: Path,
) -> None:
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    coordinator.execute(
        "create_trial", "app_first", actor_ref="user:owner",
        subnet_ref="subnet:home", capability="applications.develop",
        expected_revision=1, idempotency_key="shared-key",
        intent={"revision": "change.1"}, callback=lambda: {"ok": True},
    )

    with pytest.raises(ApplicationDevelopmentError, match="authority or intent"):
        coordinator.execute(
            "create_trial", "app_second", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.develop",
            expected_revision=1, idempotency_key="shared-key",
            intent={"revision": "change.1"}, callback=lambda: {"ok": True},
        )


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


def test_development_coordinator_recovers_only_for_original_owner(tmp_path: Path) -> None:
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    with pytest.raises(TimeoutError):
        coordinator.execute(
            "preview", "app_test", actor_ref="user:owner",
            subnet_ref="subnet:home", capability="applications.develop",
            expected_revision=1, idempotency_key="preview-1",
            intent={"source_webspace_id": "desktop"},
            callback=lambda: (_ for _ in ()).throw(TimeoutError("response lost")),
        )

    with pytest.raises(ApplicationDevelopmentError, match="original actor"):
        coordinator.recover(
            coordinator.list()[0]["operation_id"],
            actor_ref="user:other",
            subnet_ref="subnet:home",
            capability="applications.recover",
            callback=lambda _operation: {"ok": True},
        )

    recovered = coordinator.recover(
        coordinator.list()[0]["operation_id"],
        actor_ref="user:owner",
        subnet_ref="subnet:home",
        capability="applications.recover",
        callback=lambda operation: {
            "ok": True,
            "replayed_intent": dict(operation["intent"]),
        },
    )

    assert recovered["status"] == "succeeded"
    assert recovered["recovery_attempt"] == 1
    assert recovered["result"]["replayed_intent"] == {
        "source_webspace_id": "desktop"
    }
