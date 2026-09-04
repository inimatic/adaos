"""Resource management services with lazy public exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_PROTOTYPE_EXPORTS = {
    "PROTOTYPE_RESOURCE_SCHEMA",
    "PROTOTYPE_RESOURCE_STATE_SCHEMA",
    "PrototypeResourceConflict",
    "PrototypeResourceService",
}
_LOCAL_EXPORTS = {
    "LOCAL_RESOURCE_SCHEMA",
    "LOCAL_RESOURCE_STATE_SCHEMA",
    "LocalCrudResourceService",
    "LocalResourceConflict",
    "declaration_paths",
    "validate_local_resource_bundle",
}
_WORKBENCH_EXPORTS = {
    "RESOURCE_DEFINITION_SCHEMA",
    "RESOURCE_EVENT_SCHEMA",
    "RESOURCE_OPERATION_SCHEMA",
    "RESOURCE_QUERY_SCHEMA",
    "RESOURCE_TRACE_SCHEMA",
    "ResourceAccessDenied",
    "ResourceConflict",
    "ResourceWorkbenchService",
}


def __getattr__(name: str) -> Any:
    if name in _LOCAL_EXPORTS:
        return getattr(import_module("adaos.services.resources.local"), name)
    if name in _PROTOTYPE_EXPORTS:
        return getattr(import_module("adaos.services.resources.prototype"), name)
    if name in _WORKBENCH_EXPORTS:
        return getattr(import_module("adaos.services.resources.workbench"), name)
    raise AttributeError(name)

__all__ = [
    "RESOURCE_DEFINITION_SCHEMA",
    "RESOURCE_EVENT_SCHEMA",
    "RESOURCE_OPERATION_SCHEMA",
    "RESOURCE_QUERY_SCHEMA",
    "RESOURCE_TRACE_SCHEMA",
    "LOCAL_RESOURCE_SCHEMA",
    "LOCAL_RESOURCE_STATE_SCHEMA",
    "PROTOTYPE_RESOURCE_SCHEMA",
    "PROTOTYPE_RESOURCE_STATE_SCHEMA",
    "PrototypeResourceConflict",
    "PrototypeResourceService",
    "LocalCrudResourceService",
    "LocalResourceConflict",
    "declaration_paths",
    "validate_local_resource_bundle",
    "ResourceAccessDenied",
    "ResourceConflict",
    "ResourceWorkbenchService",
]
