"""Owner-facing development console API. Review authority is never an LLM tool."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.companion.ledger import Ledger
from adaos.services.companion.policy import enabled


def lab_enabled():
    if not enabled():
        raise HTTPException(404, "companion_disabled")


router = APIRouter(tags=["companion-development-lab"], dependencies=[Depends(require_token), Depends(lab_enabled)])


def actor(request: Request) -> str:
    from adaos.services.personalization_runtime import current_user_id

    return "user:" + str(current_user_id())


def owned(ledger: Ledger, session_id: str, request: Request):
    session = next((s for s in ledger.sessions(limit=100) if s["session_id"] == session_id), None)
    if not session or session["actor"] != actor(request):
        raise HTTPException(404, "session_not_found")
    return session


class Feedback(BaseModel):
    turn_id: str
    label: Literal["correct", "partial", "wrong_information", "wrong_action_target", "rephrasing_required", "too_slow", "capability_absent", "correction"]
    correction: str = Field(default="", max_length=8000)


class Review(BaseModel):
    target: str
    kind: Literal["hypothesis", "anomaly", "label"]
    payload: dict


@router.get("/state")
def state(request: Request, webspace_id: str = "desktop"):
    ledger = Ledger()
    scope = webspace_id+":"+actor(request)
    sessions = ledger.sessions(scope=scope)
    result = {"enabled": True, "mode": "development", "sessions": sessions, "items": [],
              "session_id": None, "turn_id": None, "state": "awaiting_session", "canonical_mutations": 0}
    if not sessions:
        return result
    latest = sessions[0]
    records = ledger.records(latest["session_id"])
    reviews = ledger.reviews(latest["session_id"])
    turns = [r for r in records if r["kind"] == "turn"]
    result.update(session_id=latest["session_id"], turn_id=turns[-1]["turn"] if turns else None,
                  state="sealed" if latest["sealed_at"] else "active", reviews=reviews)
    for turn in reversed(turns):
        evidence = [r for r in records if r["turn"] == turn["turn"]]
        response = next((r["payload"] for r in reversed(evidence) if r["kind"] == "response"), {})
        result["items"].append({"id": turn["turn"], "title": turn["payload"]["text"], "subtitle": response.get("message"),
                                "status": "failed" if response.get("error") else "review_required",
                                "duration_ms": response.get("duration_ms"), "details": {"evidence": evidence,
                                "reviews": [r for r in reviews if r["target"] == turn["turn"]]}})
    return result


@router.get("/sessions/{session_id}")
def session_detail(session_id: str, request: Request):
    ledger = Ledger()
    manifest = owned(ledger, session_id, request)
    return {"manifest": manifest, "records": ledger.records(session_id), "reviews": ledger.reviews(session_id)}


@router.post("/sessions/{session_id}/seal")
def seal(session_id: str, request: Request):
    ledger = Ledger()
    owned(ledger, session_id, request)
    return ledger.seal(session_id)


@router.post("/sessions/{session_id}/feedback")
def feedback(session_id: str, body: Feedback, request: Request):
    ledger = Ledger()
    owned(ledger, session_id, request)
    try:
        return ledger.feedback(session_id, body.turn_id, label=body.label, correction=body.correction, actor=actor(request))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/sessions/{session_id}/workbench")
def workbench(session_id: str, request: Request):
    ledger = Ledger()
    owned(ledger, session_id, request)
    try:
        return ledger.workbench(session_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/sessions/{session_id}/review")
def review(session_id: str, body: Review, request: Request):
    ledger = Ledger()
    owned(ledger, session_id, request)
    try:
        ledger.bundle(session_id)
        return ledger.review(session_id, body.target, kind=body.kind, actor=actor(request), payload=body.payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
