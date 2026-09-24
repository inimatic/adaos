"""Builder-to-Application API bridge for CBS semantic compilations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.domain.capability_binding_state import BindingInstance, StateSpace
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.applications.cbs import ApplicationCBSConflict, ApplicationCBSService
from adaos.services.builder.workflow import BuilderWorkflowError
from adaos.services.capability_binding_state import (
    LocalIdentityConflict,
    LocalIdentityStore,
    inspect_state_identity,
)


router = APIRouter(tags=["application-cbs"], dependencies=[Depends(require_token)])


class CBSCompilationRequest(BaseModel):
    acceptance: dict[str, Any]
    environment_profile_ref: str = Field(default="profile:local/default", pattern="^profile:")
    allowed_modes: list[str] = Field(default_factory=lambda: ["simulation", "production"])
    expected_previous_digest: str | None = Field(
        default=None,
        pattern="^sha256:[a-f0-9]{64}$",
    )


class CBSSemanticViabilityRequest(BaseModel):
    capability_contracts: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    evidence_claims: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    evidence_assessments: list[dict[str, Any]] = Field(default_factory=list, max_length=256)


def _service(ctx: AgentContext) -> ApplicationCBSService:
    return ApplicationCBSService(Path(ctx.paths.state_dir()).resolve())


def _identity_store(ctx: AgentContext) -> LocalIdentityStore:
    return LocalIdentityStore(
        Path(ctx.paths.state_dir()).resolve() / "capability-binding-state" / "identities"
    )


@router.post("/v1/applications/{application_ref}/cbs/compilations")
def compile_application_cbs(
    application_ref: str,
    body: CBSCompilationRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        compilation = _service(ctx).compile_and_register(
            application_ref=application_ref,
            acceptance=body.acceptance,
            environment_profile_ref=body.environment_profile_ref,
            allowed_modes=body.allowed_modes,
            expected_previous_digest=body.expected_previous_digest,
        )
    except ApplicationCBSConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (BuilderWorkflowError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"ok": True, "compilation": compilation, "activation_performed": False}


@router.get("/v1/applications/{application_ref}/cbs")
def inspect_application_cbs(
    application_ref: str,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        service = _service(ctx)
        compilation = service.inspect(application_ref)
        admission = service.inspect_admission(application_ref)
    except (ApplicationCBSConflict, BuilderWorkflowError, OSError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if compilation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CBS compilation not found")
    return {
        "ok": True,
        "compilation": compilation,
        "admission": admission,
        "activation_performed": False,
    }


@router.post("/v1/applications/{application_ref}/cbs/semantic-viability")
def assess_application_cbs(
    application_ref: str,
    body: CBSSemanticViabilityRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return _service(ctx).semantic_viability(
            application_ref,
            capability_contracts=body.capability_contracts,
            evidence_claims=body.evidence_claims,
            evidence_assessments=body.evidence_assessments,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="CBS compilation not found") from exc
    except (ApplicationCBSConflict, BuilderWorkflowError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/v1/cbs/state-spaces/inspect")
def inspect_cbs_state_space(
    state_space_ref: str,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Inspect local state identity; this endpoint has no mutation authority."""

    store = _identity_store(ctx)
    try:
        state_space = store.latest(state_space_ref, StateSpace)
        if state_space is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="StateSpace not found",
            )
        custodian_ref = str(state_space.to_dict()["custodian_binding_instance_ref"])
        binding = store.latest(custodian_ref, BindingInstance)
        inspection = inspect_state_identity(
            state_space,
            binding_instance=binding,
            observations=store.observations(
                subject_ref=state_space.stable_ref,
                subject_revision_digest=state_space.digest,
            ),
        )
    except HTTPException:
        raise
    except (LocalIdentityConflict, OSError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"ok": True, "inspection": inspection, "activation_performed": False}


__all__ = ["router"]
