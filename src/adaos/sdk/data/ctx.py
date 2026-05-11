from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from adaos.sdk.core._ctx import require_ctx
from adaos.services.scenario import ProjectionService
from adaos.services.user.profile import UserProfileService

_SET_BRIDGE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="adaos-ctx-set")


class _ScopeCtx:
    def __init__(self, scope: str) -> None:
        self._scope = scope

    async def set_async(
        self,
        slot: str,
        value: Any,
        *,
        user_id: Optional[str] = None,
        webspace_id: Optional[str] = None,
    ) -> None:
        """
        Async variant for use inside async skills/handlers.
        """
        ctx = require_ctx(f"sdk.data.ctx.{self._scope}.set")
        svc = ProjectionService.from_ctx(ctx)
        await svc.apply(self._scope, slot, value, user_id=user_id, webspace_id=webspace_id)

    def set(
        self,
        slot: str,
        value: Any,
        *,
        user_id: Optional[str] = None,
        webspace_id: Optional[str] = None,
    ) -> None:
        """
        Synchronous helper for ctx.<scope>.set(slot, value).

        The call is intentionally durable: when invoked from a synchronous
        tool handler that is already running inside an event loop, bridge the
        projection through a small worker loop and wait for completion instead
        of scheduling a fire-and-forget task that may outlive the handler.
        """
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.set_async(slot, value, user_id=user_id, webspace_id=webspace_id))
            return

        ctx = require_ctx(f"sdk.data.ctx.{self._scope}.set")

        async def _runner() -> None:
            svc = ProjectionService.from_ctx(ctx)
            await svc.apply(self._scope, slot, value, user_id=user_id, webspace_id=webspace_id)

        _SET_BRIDGE_EXECUTOR.submit(lambda: asyncio.run(_runner())).result()


class _CurrentUserCtx(_ScopeCtx):
    def __init__(self) -> None:
        super().__init__("current_user")

    def get_profile_settings(self) -> dict:
        ctx = require_ctx("sdk.data.ctx.current_user.get_profile_settings")
        svc = UserProfileService(ctx)
        return svc.get_profile().settings


subnet = _ScopeCtx("subnet")
current_user = _CurrentUserCtx()
selected_user = _ScopeCtx("selected_user")

__all__ = ["subnet", "current_user", "selected_user"]
