"""Stable Builder release and rollback presentation facade."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def rollback_plan(applied_release: Mapping[str, Any], *, surface_kind: str) -> dict[str, Any]:
    from adaos.services.builder.release_evidence import rollback_plan as _rollback_plan

    return dict(_rollback_plan(applied_release, surface_kind=surface_kind))


__all__ = ["rollback_plan"]
