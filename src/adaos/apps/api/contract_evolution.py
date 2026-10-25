"""Authenticated read-only Contract Evolution projections."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.contract_evolution.service import ContractEvolutionService, WAVE
from adaos.services.contract_evolution.store import EvolutionQueryLimitError


router = APIRouter(tags=["contract-evolution"], dependencies=[Depends(require_token)])


def _service(ctx):
    return ContractEvolutionService(Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir()))


@router.get("/v1/contract-evolution/applications/{application_id}")
def application_posture(application_id: str, consumer_digest: str | None = None, ctx: AgentContext = Depends(get_ctx)):
    service = _service(ctx)
    if consumer_digest is None:
        from adaos.services.applications.store import ApplicationStore
        try:
            consumer_digest = ApplicationStore(service.state_dir).get_installation(application_id).installed_release_digest
        except FileNotFoundError:
            # No installed release is not an invitation to aggregate history.
            consumer_digest = ""
    return service.application_projection("application:" + application_id, consumer_digest=consumer_digest)


@router.get("/v1/contract-evolution/waves")
def waves(ctx: AgentContext = Depends(get_ctx)):
    service = _service(ctx)
    try:
        return {"items": [service.wave_projection(item["wave_id"]) for item in service.store.list(WAVE, limit=100)]}
    except EvolutionQueryLimitError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/v1/contract-evolution/events")
def events(after: int = 0, limit: int = 100, ctx: AgentContext = Depends(get_ctx)):
    try:
        return _service(ctx).store.events(after=after, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
