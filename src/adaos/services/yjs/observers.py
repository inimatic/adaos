from __future__ import annotations

import logging
from typing import Callable, List, Tuple

import y_py as Y

_log = logging.getLogger("adaos.yjs.observers")

RoomObserver = Callable[[str, Y.YDoc], None]

_OBSERVERS: List[RoomObserver] = []


def register_room_observer(observer: RoomObserver) -> None:
    """
    Register a callback that will be invoked for each Yjs room/YDoc.

    The observer receives ``(webspace_id, ydoc)`` and may attach its own
    ``observe_*`` hooks or perform one-off initialization. Errors are logged
    but do not affect other observers.
    """
    if observer in _OBSERVERS:
        return
    _OBSERVERS.append(observer)
    try:
        name = getattr(observer, "__name__", repr(observer))
    except Exception:  # pragma: no cover - defensive logging only
        name = repr(observer)
    _log.debug("room observer registered observer=%s total=%d", name, len(_OBSERVERS))


def list_room_observers() -> Tuple[RoomObserver, ...]:
    """
    Return a snapshot of registered room observers.
    """
    return tuple(_OBSERVERS)


def attach_room_observers(webspace_id: str, ydoc: Y.YDoc) -> None:
    """
    Invoke all registered room observers for the given webspace/YDoc.

    This is called by the Y gateway when a YRoom is created or reused.
    """
    for observer in list_room_observers():
        try:
            observer(webspace_id, ydoc)
        except Exception:  # pragma: no cover - defensive logging only
            name = getattr(observer, "__name__", repr(observer))
            _log.warning(
                "room observer failed observer=%s webspace=%s",
                name,
                webspace_id,
                exc_info=True,
            )

