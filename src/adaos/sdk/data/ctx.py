from __future__ import annotations

import logging
from typing import Any, Optional

from adaos.sdk.core._ctx import require_ctx
from adaos.services.user.profile import UserProfileService

_LOG = logging.getLogger("adaos.sdk.data.ctx")


class _ProjectionServiceProxy:
    @staticmethod
    def from_ctx(ctx=None):
        from adaos.services.scenario import ProjectionService as _ProjectionService

        return _ProjectionService.from_ctx(ctx)


ProjectionService = _ProjectionServiceProxy


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

        Outside an event loop the call is durable and waits for completion.
        Inside an event loop it must not block the loop; schedule the async
        projection and return immediately. Async handlers should call
        set_async() directly when they need explicit completion semantics.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            ctx = require_ctx(f"sdk.data.ctx.{self._scope}.set")
            svc = ProjectionService.from_ctx(ctx)
            svc.apply_sync(
                self._scope,
                slot,
                value,
                user_id=user_id,
                webspace_id=webspace_id,
            )
            return

        task = loop.create_task(self.set_async(slot, value, user_id=user_id, webspace_id=webspace_id))

        def _log_projection_error(done: "asyncio.Task[None]") -> None:
            try:
                done.result()
            except Exception:
                _LOG.warning(
                    "async projection scheduled by sync ctx.%s.set failed slot=%s webspace_id=%s",
                    self._scope,
                    slot,
                    webspace_id,
                    exc_info=True,
                )

        task.add_done_callback(_log_projection_error)


class _CurrentUserCtx(_ScopeCtx):
    def __init__(self) -> None:
        super().__init__("current_user")

    def get_profile_settings(self) -> dict:
        ctx = require_ctx("sdk.data.ctx.current_user.get_profile_settings")
        svc = UserProfileService(ctx)
        return svc.get_profile().settings

    def profile(self) -> dict:
        ctx = require_ctx("sdk.data.ctx.current_user.profile")
        svc = UserProfileService(ctx)
        profile = svc.get_profile()
        return {
            "user_id": profile.user_id,
            "display_name": profile.display_name,
            "preferred_name": profile.preferred_name,
            "locale": profile.locale,
            "language": profile.language,
            "timezone": profile.timezone,
            "avatar_ref": profile.avatar_ref,
            "settings": dict(profile.settings),
            "preferences": dict(profile.preferences),
            "schema_version": profile.schema_version,
        }

    def update_profile(self, patch: dict) -> dict:
        ctx = require_ctx("sdk.data.ctx.current_user.update_profile")
        svc = UserProfileService(ctx)
        profile = svc.update_profile(dict(patch or {}))
        return dict(profile.settings)

    def preferences(self) -> dict:
        ctx = require_ctx("sdk.data.ctx.current_user.preferences")
        svc = UserProfileService(ctx)
        return svc.get_preferences()

    def update_preferences(self, patch: dict, *, device_override: bool = False) -> dict:
        ctx = require_ctx("sdk.data.ctx.current_user.update_preferences")
        svc = UserProfileService(ctx)
        return svc.update_preferences(dict(patch or {}), device_override=device_override)

    def header_settings(self) -> dict:
        ctx = require_ctx("sdk.data.ctx.current_user.header_settings")
        svc = UserProfileService(ctx)
        return svc.header_settings()


subnet = _ScopeCtx("subnet")
current_user = _CurrentUserCtx()
selected_user = _ScopeCtx("selected_user")

__all__ = ["subnet", "current_user", "selected_user"]
