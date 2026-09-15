from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.application_registry_projection import ApplicationRegistryProjection


router = APIRouter(tags=["application-registry"], dependencies=[Depends(require_token)])


def _service(ctx: AgentContext) -> ApplicationRegistryProjection:
    return ApplicationRegistryProjection(Path(ctx.paths.state_dir()).resolve())


@router.get("/diagnostics")
def get_application_registry_diagnostics(
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    return _service(ctx).diagnostics()
