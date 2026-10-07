from pathlib import Path
from threading import Event

from adaos.apps.api import component_updates as api


def test_read_does_not_wait_for_recovery_and_coalesces_rooms(tmp_path, monkeypatch):
    entered, unblock = Event(), Event()
    calls = []

    class Service:
        state_dir = tmp_path

        def reconcile_builder_sessions(self):
            calls.append("archive")
            entered.set()
            assert unblock.wait(5)

        def reconcile_local_trials(self, room):
            calls.append(room)

        def list_notices(self, **kwargs):
            return [{"notice_id": "persisted", "unread": True}]

    reconciler = api._NoticeReconciler()
    monkeypatch.setattr(api, "_NOTICE_RECONCILER", reconciler)
    service = Service()
    response = api.list_component_updates(service=service, webspace_id="desktop")
    assert response["items"][0]["notice_id"] == "persisted"
    assert entered.wait(2)
    worker = reconciler.worker
    try:
        for _ in range(20):
            reconciler.schedule(service, "desktop")
        reconciler.schedule(service, "desktop-dev")
        assert len(reconciler.pending) == 1
    finally:
        unblock.set()
        worker.join(5)
    assert calls == ["archive", "desktop", "desktop-dev"]
    assert reconciler.worker is None


def test_failed_recovery_can_retry_without_poisoning_worker(tmp_path):
    calls = []

    class Service:
        state_dir = Path(tmp_path)

        def reconcile_builder_sessions(self):
            calls.append("archive")
            if len(calls) == 1:
                raise OSError("unavailable")

        def reconcile_local_trials(self, room):
            calls.append(room)

    reconciler = api._NoticeReconciler()
    service = Service()
    # Drive the worker deterministically without a thread.
    reconciler.pending[(str(tmp_path), "desktop")] = service
    reconciler._run()
    reconciler.pending[(str(tmp_path), "desktop")] = service
    reconciler._run()
    assert calls == ["archive", "archive", "desktop"]
