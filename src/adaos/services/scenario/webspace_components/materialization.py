from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor


class MaterializationExecutorOwner:
    """Own the bounded CPU executor used by webspace materialization."""

    def __init__(self) -> None:
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    def get(self, *, max_workers: int) -> ThreadPoolExecutor:
        executor = self._executor
        if executor is not None:
            return executor
        with self._lock:
            executor = self._executor
            if executor is None:
                executor = ThreadPoolExecutor(
                    max_workers=max(1, int(max_workers)),
                    thread_name_prefix="adaos-materialize",
                )
                self._executor = executor
        return executor

    def shutdown(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
