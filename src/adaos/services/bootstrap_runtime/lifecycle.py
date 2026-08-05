from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


class BootstrapLifecycleCoordinator:
    """Own bootstrap task and one-shot lifecycle state.

    BootstrapService keeps orchestration and compatibility facades, while this
    coordinator is the single owner of task registration, boot serialization,
    readiness state, and task cancellation.
    """

    def __init__(self) -> None:
        self._boot_tasks: list[asyncio.Task[Any]] = []
        self._boot_lock = asyncio.Lock()
        self._boot_done = asyncio.Event()
        self._boot_done.set()
        self._boot_in_progress = False
        self._ready = asyncio.Event()
        self._booted = False
        self._app: Any = None
        self._member_ready_callback: Callable[[], Awaitable[None]] | None = None

    # Read-compatible views are intentionally retained while BootstrapService
    # callers migrate. Runtime mutation goes through the coordinator methods
    # below so task replacement and lifecycle transitions stay atomic.
    @property
    def boot_tasks(self) -> list[asyncio.Task[Any]]:
        return self._boot_tasks

    @boot_tasks.setter
    def boot_tasks(self, value: list[asyncio.Task[Any]]) -> None:
        self._boot_tasks = value

    @property
    def boot_lock(self) -> asyncio.Lock:
        return self._boot_lock

    @property
    def boot_done(self) -> asyncio.Event:
        return self._boot_done

    @property
    def boot_in_progress(self) -> bool:
        return self._boot_in_progress

    @boot_in_progress.setter
    def boot_in_progress(self, value: bool) -> None:
        self._boot_in_progress = bool(value)

    @property
    def ready(self) -> asyncio.Event:
        return self._ready

    @property
    def booted(self) -> bool:
        return self._booted

    @booted.setter
    def booted(self, value: bool) -> None:
        self._booted = bool(value)

    @property
    def app(self) -> Any:
        return self._app

    @app.setter
    def app(self, value: Any) -> None:
        self._app = value

    @property
    def member_ready_callback(self) -> Callable[[], Awaitable[None]] | None:
        return self._member_ready_callback

    @member_ready_callback.setter
    def member_ready_callback(self, value: Callable[[], Awaitable[None]] | None) -> None:
        self._member_ready_callback = value

    def find_live_task(self, task_name: str) -> asyncio.Task[Any] | None:
        live_tasks: list[asyncio.Task[Any]] = []
        found: asyncio.Task[Any] | None = None
        for task in self._boot_tasks:
            if task.done():
                continue
            live_tasks.append(task)
            if found is None and task.get_name() == task_name:
                found = task
        if len(live_tasks) != len(self._boot_tasks):
            self._boot_tasks = live_tasks
        return found

    def start_task_once(
        self,
        task_name: str,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> asyncio.Task[Any]:
        existing = self.find_live_task(task_name)
        if existing is not None:
            return existing
        task = asyncio.create_task(coro_factory(), name=task_name)
        self._boot_tasks.append(task)
        return task

    def track_task(self, task: asyncio.Task[Any]) -> asyncio.Task[Any]:
        """Adopt an already-created task into the current boot generation."""
        if task not in self._boot_tasks:
            self._boot_tasks.append(task)
        return task

    def replace_task(
        self,
        task_name: str,
        coro_factory: Callable[[], Awaitable[Any]],
    ) -> tuple[asyncio.Task[Any], bool]:
        """Replace a named task and return ``(new_task, cancelled_previous)``."""
        existing = self.find_live_task(task_name)
        cancelled_previous = existing is not None
        if existing is not None:
            existing.cancel()
            self._boot_tasks = [task for task in self._boot_tasks if task is not existing]
        task = asyncio.create_task(coro_factory(), name=task_name)
        self._boot_tasks.append(task)
        return task, cancelled_previous

    def bind_app(self, app: Any) -> None:
        self._app = app

    def signal_ready(self) -> None:
        self._ready.set()

    def mark_ready(self) -> None:
        self.signal_ready()
        self._booted = True

    def mark_booted(self) -> None:
        self._booted = True

    def set_member_ready_callback(
        self,
        callback: Callable[[], Awaitable[None]] | None,
    ) -> None:
        self._member_ready_callback = callback

    async def run_once(
        self,
        app: Any,
        boot: Callable[[Any], Awaitable[None]],
    ) -> None:
        while True:
            async with self._boot_lock:
                if self._booted:
                    return
                if not self._boot_in_progress:
                    self._boot_in_progress = True
                    self._boot_done.clear()
                    break
                boot_done = self._boot_done
            await boot_done.wait()
        try:
            await boot(app)
        finally:
            async with self._boot_lock:
                self._boot_in_progress = False
                self._boot_done.set()

    async def stop(self) -> None:
        for task in list(self._boot_tasks):
            try:
                task.cancel()
            except Exception:
                pass
        if self._boot_tasks:
            await asyncio.gather(*self._boot_tasks, return_exceptions=True)
            self._boot_tasks.clear()
        self._boot_in_progress = False
        self._boot_done.set()
        self._booted = False
        self._ready.clear()
        self._member_ready_callback = None

    def is_ready(self) -> bool:
        return self._ready.is_set()
