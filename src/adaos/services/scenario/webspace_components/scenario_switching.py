from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from .state import WebspaceTaskState


@dataclass(frozen=True)
class ScenarioSwitchRequest:
    webspace_id: str
    scenario_id: str
    set_home: bool
    wait_for_rebuild: bool
    request_id: str | None
    request_source: str | None
    request_client: str | None


@dataclass(frozen=True)
class ScenarioSwitchDecision:
    action: str
    reason: str | None = None


class WebspaceScenarioSwitchingService:
    """Own request normalization and transition policy for scenario switches."""

    @staticmethod
    def mode() -> str:
        return "pointer_only"

    @staticmethod
    def normalize_request(
        webspace_id: Any,
        scenario_id: Any,
        *,
        set_home: bool | None,
        wait_for_rebuild: bool,
        request_id: str | None,
        request_source: str | None,
        request_client: str | None,
    ) -> ScenarioSwitchRequest:
        normalized_webspace_id = str(webspace_id or "").strip()
        normalized_scenario_id = str(scenario_id or "").strip()
        if not normalized_webspace_id:
            raise ValueError("webspace_id is required")
        if not normalized_scenario_id:
            raise ValueError("scenario_id is required")
        return ScenarioSwitchRequest(
            webspace_id=normalized_webspace_id,
            scenario_id=normalized_scenario_id,
            set_home=bool(set_home) if set_home is not None else False,
            wait_for_rebuild=bool(wait_for_rebuild),
            request_id=str(request_id or "").strip() or None,
            request_source=str(request_source or "").strip() or None,
            request_client=str(request_client or "").strip() or None,
        )

    @staticmethod
    def decide(
        *,
        current_scenario: Any,
        target_scenario: str,
        rebuild_state: Mapping[str, Any],
        materialization_matches_target: bool,
    ) -> ScenarioSwitchDecision:
        current_matches = str(current_scenario or "").strip() == target_scenario
        rebuild_matches = str(rebuild_state.get("scenario_id") or "").strip() == target_scenario
        pending = bool(rebuild_state.get("pending"))
        status = str(rebuild_state.get("status") or "").strip().lower()
        if current_matches and not pending and status == "ready" and rebuild_matches and materialization_matches_target:
            return ScenarioSwitchDecision(action="skip", reason="already_current_ready")
        if current_matches and pending and rebuild_matches:
            return ScenarioSwitchDecision(action="join", reason="already_pending_rebuild")
        return ScenarioSwitchDecision(action="switch")

    @staticmethod
    def loader_space(row: Any) -> str:
        try:
            return str(row.effective_source_mode or "").strip() or "workspace"
        except Exception:
            return "workspace"

    @staticmethod
    async def _notify(
        callback: Callable[..., Any] | None,
        *args: Any,
    ) -> None:
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    def schedule_rebuild(
        self,
        *,
        task_state: WebspaceTaskState,
        webspace_id: str,
        scenario_id: str,
        operation: Callable[[], Awaitable[Any]],
        on_cancel: Callable[[], Any] | None = None,
        on_error: Callable[[Exception], Any] | None = None,
    ) -> asyncio.Task[Any]:
        """Own replacement, completion cleanup, and failure boundaries."""

        async def _runner() -> None:
            try:
                await operation()
            except asyncio.CancelledError:
                await self._notify(on_cancel)
                raise
            except Exception as exc:
                await self._notify(on_error, exc)
            finally:
                task_state.pop_task(
                    task_state.SCENARIO_SWITCH,
                    webspace_id,
                    expected=task,
                )

        task = asyncio.create_task(
            _runner(),
            name=f"webspace-scenario-switch:{webspace_id}:{scenario_id}",
        )
        task_state.put_task(
            task_state.SCENARIO_SWITCH,
            webspace_id,
            task,
            cancel_existing=True,
        )
        return task

    @staticmethod
    async def await_existing_rebuild(
        task_state: WebspaceTaskState,
        webspace_id: str,
    ) -> bool:
        task = task_state.active_task(task_state.SCENARIO_SWITCH, webspace_id)
        if task is None:
            return False
        try:
            await asyncio.shield(task)
        except Exception:
            pass
        return True
