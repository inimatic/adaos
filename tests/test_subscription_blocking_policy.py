from __future__ import annotations

from adaos.sdk.core import decorators
from adaos.sdk.data import bus


def test_sync_skill_handlers_run_off_owner_loop_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SYNC_SUBSCRIPTION_TO_THREAD", raising=False)
    monkeypatch.delenv("ADAOS_SYNC_SUBSCRIPTION_THREAD_TOPICS", raising=False)
    monkeypatch.delenv("ADAOS_SYNC_SUBSCRIPTION_LOOP_TOPICS", raising=False)

    for topic in ("core.update.status", "hub.core_update.status", "operations.changed", "custom.evolved"):
        assert bus._run_sync_handler_in_thread(topic) is True
        assert decorators._run_sync_subscription_in_thread(topic) is True


def test_owner_loop_execution_requires_explicit_topic_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SYNC_SUBSCRIPTION_LOOP_TOPICS", "core.internal.fast")

    assert bus._run_sync_handler_in_thread("core.internal.fast") is False
    assert decorators._run_sync_subscription_in_thread("core.internal.fast") is False
    assert (
        decorators._run_sync_subscription_in_thread(
            "core.internal.fast",
            skill_name="evolving_skill",
        )
        is True
    )
    assert bus._run_sync_handler_in_thread("skill.changed") is True
    assert decorators._run_sync_subscription_in_thread("skill.changed") is True


def test_skill_handler_executor_boundary_cannot_be_disabled_by_process_policy(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SYNC_SUBSCRIPTION_TO_THREAD", "0")
    monkeypatch.setenv("ADAOS_SYNC_SUBSCRIPTION_LOOP_TOPICS", "*")

    assert decorators._run_sync_subscription_in_thread("node.yjs.control.completed") is False
    assert (
        decorators._run_sync_subscription_in_thread(
            "node.yjs.control.completed",
            skill_name="infrastate_skill",
        )
        is True
    )
