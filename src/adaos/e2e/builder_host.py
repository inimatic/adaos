"""Bind sequential E2E cases to an existing Builder, never a synthetic host."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


def require_builder_host(webspace_id: str | None = None) -> str:
    from adaos.services.builder.workbench import BuilderWorkbenchService

    requested = str(webspace_id or os.getenv("ADAOS_BUILDER_E2E_WEBSPACE_ID") or "").strip()
    if not requested:
        raise ValueError(
            "Select an existing Builder with ADAOS_BUILDER_E2E_WEBSPACE_ID (or --host). "
            "Sequential cases reuse its single preview; E2E does not create Builder hosts."
        )
    service = BuilderWorkbenchService.from_context()
    service.relationships.require_preview_host(requested)
    service.resolve_builder_context(requested, require_ready=False)
    return requested


def step_builder_host(inputs: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    selected = str(context.get("webspace_id") or "").strip()
    requested = str(inputs.get("webspace_id") or selected).strip()
    if not requested or (selected and requested != selected):
        raise ValueError("E2E steps must use the single Builder host selected for the run")
    return requested
