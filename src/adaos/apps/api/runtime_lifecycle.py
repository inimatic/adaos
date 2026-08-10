from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import FastAPI

from adaos.apps.api.router_registry import mount_runtime_routers


RuntimeContextFactory = Callable[[FastAPI], AbstractAsyncContextManager[Any]]


@dataclass(slots=True)
class RuntimeApplicationLifecycle:
    """Owns application startup/teardown and the router composition boundary."""

    app: FastAPI
    runtime_context_factory: RuntimeContextFactory
    router_mount: Callable[[FastAPI], None] = mount_runtime_routers
    _runtime_context: AbstractAsyncContextManager[Any] | None = field(default=None, init=False)
    _started: bool = field(default=False, init=False)

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        self.router_mount(self.app)
        runtime_context = self.runtime_context_factory(self.app)
        await runtime_context.__aenter__()
        self._runtime_context = runtime_context
        self._started = True

    async def stop(self) -> None:
        runtime_context = self._runtime_context
        if runtime_context is None:
            return
        self._runtime_context = None
        self._started = False
        await runtime_context.__aexit__(None, None, None)
