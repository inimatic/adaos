from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


@dataclass(frozen=True, slots=True)
class WebspaceEventOperations:
    default_webspace_id: Callable[[], str]
    rebuild_webspace: Callable[..., Awaitable[Any]]
    schedule_skill_runtime_rebuild: Callable[..., Any]
    reload_publication_webspaces: Callable[[str, str], Awaitable[Any]]


class WebspaceEventService:
    """Interprets event payloads; decorators remain thin bus adapters."""

    async def scenarios_synced(self, event: Mapping[str, Any], operations: WebspaceEventOperations) -> None:
        webspace_id = str(event.get("webspace_id") or operations.default_webspace_id())
        scenario_id = str(event.get("scenario_id") or "").strip() or None
        await operations.rebuild_webspace(
            webspace_id,
            action="scenario_projection_sync",
            scenario_id=scenario_id,
            scenario_resolution="projected_payload",
            source_of_truth="scenario_projection",
        )

    def skill_changed(
        self,
        event: Mapping[str, Any],
        operations: WebspaceEventOperations,
        *,
        action: str,
        topic: str,
        allow_defer: bool,
    ) -> None:
        if allow_defer and bool(event.get("defer_webspace_rebuild")):
            return
        webspace_id = str(event.get("webspace_id") or operations.default_webspace_id())
        reason = str(event.get("skill_name") or event.get("name") or topic)
        operations.schedule_skill_runtime_rebuild(
            webspace_id=webspace_id,
            action=action,
            source_of_truth="skill_runtime",
            reason=reason,
        )

    async def scenario_removed(self, event: Mapping[str, Any], operations: WebspaceEventOperations) -> None:
        webspace_id = str(event.get("webspace_id") or operations.default_webspace_id())
        await operations.rebuild_webspace(
            webspace_id,
            action="scenario_uninstall_sync",
            source_of_truth="scenario_projection",
        )

    async def publication(
        self,
        event: Mapping[str, Any],
        operations: WebspaceEventOperations,
        *,
        object_type: str,
    ) -> None:
        object_id = str(event.get("name") or event.get(f"{object_type}_id") or "").strip()
        if object_id:
            await operations.reload_publication_webspaces(object_type, object_id)
