"""Owner control API; Management uses the same node service through the SDK."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.node_maintenance import from_context

router = APIRouter(tags=["node-maintenance"], dependencies=[Depends(require_token)])


class LocationPlan(BaseModel):
    role: str = Field(max_length=80)
    desired_path: str = Field(min_length=1, max_length=4096)
    expected_revision: int = Field(ge=0)
    quota_bytes: int | None = Field(default=None, gt=0)
    min_free_bytes: int = Field(default=20*1024**3, ge=0)


class ApplyPlan(BaseModel):
    plan_digest: str = Field(pattern="^[a-f0-9]{64}$")


class CampaignPlan(BaseModel):
    suite_id: str = Field(min_length=1, max_length=160)
    node_id: str = Field(min_length=1, max_length=160)
    target_build: str = Field(default="", max_length=128)


class MigrationPlan(BaseModel):
    role: str = Field(max_length=80)
    expected_revision: int = Field(ge=0)
    desired_path: str | None = Field(default=None, max_length=4096)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("")
def snapshot(section: str = "storage"):
    return _call(from_context().snapshot, section=section)


@router.post("/locations/plan")
def plan_location(body: LocationPlan):
    return _call(from_context().locations.plan, **body.model_dump())


@router.post("/locations/apply")
def apply_location(body: ApplyPlan):
    return _call(from_context().locations.apply, body.plan_digest)


@router.post("/migrations/plan")
def plan_migration(body: MigrationPlan):
    return _call(from_context().migrations.plan, **body.model_dump())


@router.post("/migrations/start")
def start_migration(body: ApplyPlan):
    service = from_context()
    result = _call(service.migrations.start, body.plan_digest)
    if result["status"] == "running":
        from adaos.services.node_maintenance.worker import ensure_worker
        ensure_worker(service)
    return result


@router.post("/migrations/{job_id}/resume")
def resume_migration(job_id: str):
    service = from_context()
    result = _call(service.migrations.advance, job_id, max_seconds=.05)
    if result["status"] == "running":
        from adaos.services.node_maintenance.worker import ensure_worker
        ensure_worker(service)
    return result


@router.post("/migrations/{job_id}/cancel")
def cancel_migration(job_id: str):
    return _call(from_context().migrations.cancel, job_id)


@router.post("/inventory/{role}")
def advance_inventory(role: str, restart: bool = False):
    service = from_context()
    sample = _call(service.advance_inventory, role, restart=restart)
    if sample["status"] == "running":
        from adaos.services.node_maintenance.worker import ensure_worker
        ensure_worker(service)
    return {"accepted": True, "role": role, "sample": sample}


@router.post("/testing/plan")
def plan_testing(body: CampaignPlan):
    from adaos.services.node_maintenance.validation import plan_campaign
    return _call(plan_campaign, from_context(), **body.model_dump())


@router.post("/testing/apply")
def apply_testing(body: ApplyPlan, background: BackgroundTasks):
    from adaos.services.node_maintenance.validation import apply_campaign, run_pending_campaign
    result = _call(apply_campaign, from_context(), body.plan_digest)
    if result["state"] == "pending":
        background.add_task(run_pending_campaign, result["campaign_id"])
    return result
