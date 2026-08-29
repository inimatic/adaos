from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from adaos.apps.api.auth import require_token
from adaos.services.resources import ResourceAccessDenied, ResourceConflict, ResourceWorkbenchService


router = APIRouter(tags=["resources"], dependencies=[Depends(require_token)])


def _get_service() -> ResourceWorkbenchService:
    return ResourceWorkbenchService()


class ResourceQueryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="adaos.resource.query.v1", alias="schema")
    resource_type: str = Field(..., min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)
    relation_filters: dict[str, Any] = Field(default_factory=dict)
    search: str = ""
    relevance_context: dict[str, Any] = Field(default_factory=dict)
    sort: list[Any] = Field(default_factory=list)
    cursor: str | None = None
    limit: int = Field(default=200, ge=0, le=1000)
    include: list[str] = Field(default_factory=list)
    locale: str = ""
    actor: dict[str, Any] = Field(default_factory=lambda: {"id": "api", "role": "owner"})
    subject: dict[str, Any] = Field(default_factory=dict)


class ResourceOperationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_: str = Field(default="adaos.resource.operation.v1", alias="schema")
    resource_type: str = Field(..., min_length=1)
    operation_id: str = Field(..., min_length=1)
    record_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: dict[str, Any] = Field(default_factory=lambda: {"id": "api", "role": "owner"})
    subject: dict[str, Any] = Field(default_factory=dict)
    delegation: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = ""
    expected_revision: int | str | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    locale: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


@router.get("/definitions")
def list_resource_definitions(
    resource_type: str | None = None,
    service: ResourceWorkbenchService = Depends(_get_service),
) -> dict[str, Any]:
    if resource_type:
        definition = service.definition(resource_type)
        if not definition:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"resource_definition_not_found:{resource_type}")
        definitions = [definition]
    else:
        definitions = service.definitions()
    return {"ok": True, "definitions": definitions, "items": definitions, "count": len(definitions)}


@router.get("/definitions/{resource_type:path}")
def get_resource_definition(
    resource_type: str,
    service: ResourceWorkbenchService = Depends(_get_service),
) -> dict[str, Any]:
    definition = service.definition(resource_type)
    if not definition:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"resource_definition_not_found:{resource_type}")
    return {"ok": True, "definition": definition}


@router.post("/query")
def query_resources(
    body: ResourceQueryRequest,
    service: ResourceWorkbenchService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return service.query(body.model_dump(by_alias=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/operate")
def operate_resource(
    body: ResourceOperationRequest,
    service: ResourceWorkbenchService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return service.operate(body.model_dump(by_alias=True))
    except ResourceAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ResourceConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/traces")
def list_resource_traces(
    resource_type: str | None = None,
    limit: int = Query(default=100, ge=0, le=1000),
    service: ResourceWorkbenchService = Depends(_get_service),
) -> dict[str, Any]:
    traces = service.traces(resource_type=resource_type, limit=limit)
    return {"ok": True, "traces": traces, "items": traces, "count": len(traces)}


@router.get("/events")
def list_resource_events(
    resource_type: str | None = None,
    limit: int = Query(default=100, ge=0, le=1000),
    service: ResourceWorkbenchService = Depends(_get_service),
) -> dict[str, Any]:
    events = service.events(resource_type=resource_type, limit=limit)
    return {"ok": True, "events": events, "items": events, "count": len(events)}
