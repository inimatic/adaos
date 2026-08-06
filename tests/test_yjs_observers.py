from __future__ import annotations

from adaos.services.yjs import observers as yjs_observers


def _reset_yjs_observer_state(monkeypatch) -> None:
    monkeypatch.setattr(yjs_observers, "_OBSERVERS", [])
    monkeypatch.setattr(yjs_observers, "_ATTACHED_OBSERVERS", {})
    monkeypatch.setattr(yjs_observers, "_ACTIVE_YDOC_IDS", {})


def test_attach_room_observers_is_idempotent_for_same_doc(monkeypatch) -> None:
    _reset_yjs_observer_state(monkeypatch)
    calls: list[tuple[str, int]] = []

    def _observer(webspace_id: str, ydoc) -> None:
        calls.append((webspace_id, id(ydoc)))

    ydoc = object()
    yjs_observers.register_room_observer(_observer)

    yjs_observers.attach_room_observers("default", ydoc)
    yjs_observers.attach_room_observers("default", ydoc)

    assert calls == [("default", id(ydoc))]


def test_attach_room_observers_reattaches_for_new_doc(monkeypatch) -> None:
    _reset_yjs_observer_state(monkeypatch)
    calls: list[int] = []

    def _observer(_webspace_id: str, ydoc) -> None:
        calls.append(id(ydoc))

    first_doc = object()
    second_doc = object()
    yjs_observers.register_room_observer(_observer)

    yjs_observers.attach_room_observers("default", first_doc)
    yjs_observers.attach_room_observers("default", second_doc)
    yjs_observers.attach_room_observers("default", second_doc)

    assert calls == [id(first_doc), id(second_doc)]


def test_attach_room_observers_retries_after_failed_attach(monkeypatch) -> None:
    _reset_yjs_observer_state(monkeypatch)
    attempts = 0

    def _observer(_webspace_id: str, _ydoc) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("attach failed")

    ydoc = object()
    yjs_observers.register_room_observer(_observer)

    yjs_observers.attach_room_observers("default", ydoc)
    yjs_observers.attach_room_observers("default", ydoc)

    assert attempts == 2


def test_forget_room_observers_calls_detach_callbacks(monkeypatch) -> None:
    _reset_yjs_observer_state(monkeypatch)
    detached: list[tuple[str, int]] = []

    def _observer(webspace_id: str, ydoc):
        def _detach() -> None:
            detached.append((webspace_id, id(ydoc)))

        return _detach

    ydoc = object()
    yjs_observers.register_room_observer(_observer)

    yjs_observers.attach_room_observers("default", ydoc)
    yjs_observers.forget_room_observers("default", ydoc)

    assert detached == [("default", id(ydoc))]


def test_attach_room_observers_detaches_previous_doc(monkeypatch) -> None:
    _reset_yjs_observer_state(monkeypatch)
    detached: list[int] = []

    def _observer(_webspace_id: str, ydoc):
        def _detach() -> None:
            detached.append(id(ydoc))

        return _detach

    first_doc = object()
    second_doc = object()
    yjs_observers.register_room_observer(_observer)

    yjs_observers.attach_room_observers("default", first_doc)
    yjs_observers.attach_room_observers("default", second_doc)

    assert detached == [id(first_doc)]
