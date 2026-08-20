from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable

from fastapi import FastAPI

from adaos.apps.api.router_registry import mount_runtime_routers


RuntimeContextFactory = Callable[[FastAPI], AbstractAsyncContextManager[Any]]
_log = logging.getLogger("adaos.startup")


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
        router_started = time.perf_counter()
        _log.info("startup stage start stage=mount_runtime_routers")
        try:
            self.router_mount(self.app)
        except Exception as exc:
            _log.warning(
                "startup stage failed stage=mount_runtime_routers duration_s=%.3f error=%s",
                time.perf_counter() - router_started,
                type(exc).__name__,
            )
            raise
        _log.info(
            "startup stage done stage=mount_runtime_routers duration_s=%.3f",
            time.perf_counter() - router_started,
        )
        runtime_context = self.runtime_context_factory(self.app)
        context_started = time.perf_counter()
        _log.info("startup stage start stage=runtime_context_enter")
        try:
            await runtime_context.__aenter__()
        except Exception as exc:
            _log.warning(
                "startup stage failed stage=runtime_context_enter duration_s=%.3f error=%s",
                time.perf_counter() - context_started,
                type(exc).__name__,
            )
            raise
        _log.info(
            "startup stage done stage=runtime_context_enter duration_s=%.3f",
            time.perf_counter() - context_started,
        )
        self._runtime_context = runtime_context
        self._started = True

    async def stop(self) -> None:
        runtime_context = self._runtime_context
        if runtime_context is None:
            return
        self._runtime_context = None
        self._started = False
        await runtime_context.__aexit__(None, None, None)
