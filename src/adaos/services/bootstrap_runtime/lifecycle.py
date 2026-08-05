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
        self.boot_tasks: list[asyncio.Task[Any]] = []
        self.boot_lock = asyncio.Lock()
        self.boot_done = asyncio.Event()
        self.boot_done.set()
        self.boot_in_progress = False
        self.ready = asyncio.Event()
        self.booted = False
        self.app: Any = None
        self.member_ready_callback: Callable[[], Awaitable[None]] | None = None

    def find_live_task(self, task_name: str) -> asyncio.Task[Any] | None:
        live_tasks: list[asyncio.Task[Any]] = []
        found: asyncio.Task[Any] | None = None
        for task in self.boot_tasks:
            if task.done():
                continue
            live_tasks.append(task)
            if found is None and task.get_name() == task_name:
                found = task
        if len(live_tasks) != len(self.boot_tasks):
            self.boot_tasks = live_tasks
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
        self.boot_tasks.append(task)
        return task

    async def run_once(
        self,
        app: Any,
        boot: Callable[[Any], Awaitable[None]],
    ) -> None:
        while True:
            async with self.boot_lock:
                if self.booted:
                    return
                if not self.boot_in_progress:
                    self.boot_in_progress = True
                    self.boot_done.clear()
                    break
                boot_done = self.boot_done
            await boot_done.wait()
        try:
            await boot(app)
        finally:
            async with self.boot_lock:
                self.boot_in_progress = False
                self.boot_done.set()

    async def stop(self) -> None:
        for task in list(self.boot_tasks):
            try:
                task.cancel()
            except Exception:
                pass
        if self.boot_tasks:
            await asyncio.gather(*self.boot_tasks, return_exceptions=True)
            self.boot_tasks.clear()
        self.boot_in_progress = False
        self.boot_done.set()
        self.booted = False
        self.ready.clear()

    def is_ready(self) -> bool:
        return self.ready.is_set()
