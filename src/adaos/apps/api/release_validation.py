from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.domain.types import Event
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.release_validation import (
    OBSERVE_CHECKS,
    TestNode,
    TestSuite,
    ValidationCampaign,
    get_release_validation_service,
)


router = APIRouter(tags=["release-validation"], dependencies=[Depends(require_token)])


class TestNodeRequest(BaseModel):
    node_id: str
    display_name: str
    host: str
    identity_file: str
    ssh_user: str = "root"
    ssh_port: int = 22
    runtime_port: int = 8778
    supervisor_port: int = 8776
    base_dir: str = "/root/.adaos"
    capabilities: list[str] = Field(default_factory=lambda: ["adaos.runtime.observe"])
    allowed_profiles: list[str] = Field(default_factory=lambda: ["observe"])
    enabled: bool = True


class TestSuiteRequest(BaseModel):
    suite_id: str
    version: str
    display_name: str
    checks: list[str] = Field(default_factory=lambda: list(OBSERVE_CHECKS))
    required_capabilities: list[str] = Field(default_factory=lambda: ["adaos.runtime.observe"])
    timeout_s: float = 45.0


class ValidationCampaignRequest(BaseModel):
    campaign_id: str | None = None
    suite_id: str
    target_build: str
    node_ids: list[str]
    quorum: int = 1


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'") or "not_found")
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc) or "release_validation_conflict")


def _notification_text(campaign: dict[str, Any]) -> str:
    state = str(campaign.get("state") or "unknown").upper()
    result = campaign.get("result") if isinstance(campaign.get("result"), dict) else {}
    return (
        f"AdaOS validation {state}: {campaign.get('campaign_id')}\n"
        f"Target: {campaign.get('target_build')}\n"
        f"Passed: {result.get('passed', 0)}, failed: {result.get('failed', 0)}, "
        f"inconclusive: {result.get('inconclusive', 0)}, timed out: {result.get('timed_out', 0)}"
    )


@router.get("")
def snapshot() -> dict[str, Any]:
    return get_release_validation_service().snapshot()


@router.post("/nodes", status_code=status.HTTP_201_CREATED)
def register_node(body: TestNodeRequest) -> dict[str, Any]:
    try:
        payload = body.model_dump()
        payload["capabilities"] = tuple(payload["capabilities"])
        payload["allowed_profiles"] = tuple(payload["allowed_profiles"])
        return get_release_validation_service().register_node(TestNode(**payload))
    except (ValueError, OSError) as exc:
        raise _http_error(exc) from exc


@router.post("/suites", status_code=status.HTTP_201_CREATED)
def register_suite(body: TestSuiteRequest) -> dict[str, Any]:
    try:
        payload = body.model_dump()
        payload["checks"] = tuple(payload["checks"])
        payload["required_capabilities"] = tuple(payload["required_capabilities"])
        payload["profile"] = "observe"
        return get_release_validation_service().register_suite(TestSuite(**payload))
    except (ValueError, OSError) as exc:
        raise _http_error(exc) from exc


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
def create_campaign(body: ValidationCampaignRequest) -> dict[str, Any]:
    try:
        campaign = ValidationCampaign(
            campaign_id=body.campaign_id or f"campaign-{uuid.uuid4().hex[:12]}",
            suite_id=body.suite_id,
            target_build=body.target_build,
            node_ids=tuple(body.node_ids),
            quorum=body.quorum,
        )
        return get_release_validation_service().create_campaign(campaign)
    except (KeyError, ValueError, OSError) as exc:
        raise _http_error(exc) from exc


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str) -> dict[str, Any]:
    try:
        return get_release_validation_service().campaign(campaign_id)
    except (KeyError, ValueError) as exc:
        raise _http_error(exc) from exc


@router.post("/campaigns/{campaign_id}/run")
async def run_campaign(
    campaign_id: str,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        campaign = await asyncio.to_thread(get_release_validation_service().run_campaign, campaign_id)
    except (KeyError, ValueError, OSError) as exc:
        raise _http_error(exc) from exc
    ctx.bus.publish(
        Event(
            type="ui.notify",
            payload={
                "text": _notification_text(campaign),
                "_meta": {
                    "source": "release_validation",
                    "campaign_id": campaign_id,
                    "severity": "info" if campaign.get("state") == "passed" else "critical",
                },
            },
            source="api.release_validation",
            ts=time.time(),
        )
    )
    return campaign


@router.get("/assignments/{assignment_id}")
def get_assignment(assignment_id: str) -> dict[str, Any]:
    try:
        return get_release_validation_service().assignment(assignment_id)
    except (KeyError, ValueError) as exc:
        raise _http_error(exc) from exc
