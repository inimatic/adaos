"""Resource management services."""

from adaos.services.resources.prototype import (
    PROTOTYPE_RESOURCE_SCHEMA,
    PROTOTYPE_RESOURCE_STATE_SCHEMA,
    PrototypeResourceConflict,
    PrototypeResourceService,
)
from adaos.services.resources.workbench import (
    RESOURCE_DEFINITION_SCHEMA,
    RESOURCE_EVENT_SCHEMA,
    RESOURCE_OPERATION_SCHEMA,
    RESOURCE_QUERY_SCHEMA,
    RESOURCE_TRACE_SCHEMA,
    ResourceAccessDenied,
    ResourceConflict,
    ResourceWorkbenchService,
)

__all__ = [
    "RESOURCE_DEFINITION_SCHEMA",
    "RESOURCE_EVENT_SCHEMA",
    "RESOURCE_OPERATION_SCHEMA",
    "RESOURCE_QUERY_SCHEMA",
    "RESOURCE_TRACE_SCHEMA",
    "PROTOTYPE_RESOURCE_SCHEMA",
    "PROTOTYPE_RESOURCE_STATE_SCHEMA",
    "PrototypeResourceConflict",
    "PrototypeResourceService",
    "ResourceAccessDenied",
    "ResourceConflict",
    "ResourceWorkbenchService",
]
