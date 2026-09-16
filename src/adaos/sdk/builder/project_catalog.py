"""SDK facade for Builder project catalog queries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.project_catalog import BuilderProjectCatalogService

    return BuilderProjectCatalogService.from_context()


def list_projects(**kwargs: Any) -> list[dict[str, Any]]:
    """List Builder catalog rows through the registry-backed projection service."""

    return [
        dict(item)
        for item in _service().list_projects(**kwargs)
        if isinstance(item, Mapping)
    ]


__all__ = ["list_projects"]
