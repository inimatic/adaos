from __future__ import annotations

from dataclasses import dataclass

from .builder_publication import WebspaceBuilderPublicationService
from .cache import MaterializedWebspaceDiskCache, WebspaceCacheState
from .events import WebspaceEventService
from .materialization import MaterializationExecutorOwner
from .materialization_runtime import WebspaceMaterializationService
from .projections import WebspaceProjectionService
from .rebuild import WebspaceRebuildService
from .recovery import WebspaceRecoveryCoordinator
from .resolution import WebspaceResolutionService
from .scenario_switching import WebspaceScenarioSwitchingService
from .skill_catalog import WebspaceSkillCatalogService
from .state import WebspaceTaskState
from .task_scheduling import WebspaceTaskSchedulingService


@dataclass(frozen=True, slots=True)
class WebspaceRuntimeContainer:
    """Single composition owner for stateful webspace runtime collaborators."""

    tasks: WebspaceTaskState
    scheduling: WebspaceTaskSchedulingService
    cache: WebspaceCacheState
    disk_cache: MaterializedWebspaceDiskCache
    events: WebspaceEventService
    materialization_executor: MaterializationExecutorOwner
    materialization: WebspaceMaterializationService
    projections: WebspaceProjectionService
    recovery: WebspaceRecoveryCoordinator
    rebuild: WebspaceRebuildService
    resolution: WebspaceResolutionService
    scenario_switching: WebspaceScenarioSwitchingService
    skill_catalog: WebspaceSkillCatalogService
    builder_publication: WebspaceBuilderPublicationService

    @classmethod
    def create_default(cls, *, recovery_command_cache_limit: int = 256) -> "WebspaceRuntimeContainer":
        return cls(
            tasks=WebspaceTaskState(),
            scheduling=WebspaceTaskSchedulingService(),
            cache=WebspaceCacheState(),
            disk_cache=MaterializedWebspaceDiskCache(),
            events=WebspaceEventService(),
            materialization_executor=MaterializationExecutorOwner(),
            materialization=WebspaceMaterializationService(),
            projections=WebspaceProjectionService(),
            recovery=WebspaceRecoveryCoordinator(command_cache_limit=recovery_command_cache_limit),
            rebuild=WebspaceRebuildService(),
            resolution=WebspaceResolutionService(),
            scenario_switching=WebspaceScenarioSwitchingService(),
            skill_catalog=WebspaceSkillCatalogService(),
            builder_publication=WebspaceBuilderPublicationService(),
        )
