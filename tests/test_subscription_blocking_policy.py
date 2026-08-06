from __future__ import annotations

from adaos.sdk.core import decorators
from adaos.sdk.data import bus


def test_core_update_status_sync_handlers_remain_on_owner_loop_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SYNC_SUBSCRIPTION_TO_THREAD", raising=False)
    monkeypatch.delenv("ADAOS_SYNC_SUBSCRIPTION_THREAD_TOPICS", raising=False)

    for topic in ("core.update.status", "hub.core_update.status"):
        assert bus._run_sync_handler_in_thread(topic) is False
        assert decorators._run_sync_subscription_in_thread(topic) is False
