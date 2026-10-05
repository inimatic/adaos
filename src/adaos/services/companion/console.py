"""Read models and explicit owner actions for the learning console."""
from __future__ import annotations

from .ledger import Ledger


def owner() -> str:
    from adaos.services.personalization_runtime import current_user_id

    return "user:" + current_user_id()


def snapshot(webspace: str) -> dict:
    ledger = Ledger()
    sessions = ledger.sessions(scope=webspace+":"+owner())
    result = {"enabled": True, "mode": "development", "sessions": sessions, "items": [],
              "session_id": None, "turn_id": None, "state": "awaiting_session", "canonical_mutations": 0}
    if not sessions:
        return result
    latest = sessions[0]
    records = ledger.records(latest["session_id"])
    reviews = ledger.reviews(latest["session_id"])
    turns = [r for r in records if r["kind"] == "turn"]
    result.update(session_id=latest["session_id"], turn_id=turns[-1]["turn"] if turns else None,
                  state="sealed" if latest["sealed_at"] else "active", reviews=reviews,
                  hypotheses=ledger.hypotheses(latest["session_id"]))
    for turn in reversed(turns):
        evidence = [r for r in records if r["turn"] == turn["turn"]]
        response = next((r["payload"] for r in reversed(evidence) if r["kind"] == "response"), {})
        result["items"].append({"id": turn["turn"], "title": turn["payload"]["text"], "subtitle": response.get("message"),
                                "status": "failed" if response.get("error") else "review_required",
                                "duration_ms": response.get("duration_ms"), "details": {"evidence": evidence,
                                "reviews": [r for r in reviews if r["target"] == turn["turn"]]}})
    result["diagnostics"] = [
        {"id": r["id"], "title": r["kind"], "subtitle": str(r["payload"].get("tool") or r["payload"].get("model") or ""),
         "duration_ms": r["payload"].get("duration_ms"), "details": r["payload"]}
        for r in reversed(records) if r["kind"] in {"tool_result", "model_call", "receipt", "observation", "anomaly", "hypothesis"}]
    if latest["sealed_at"]:
        result["workbench"] = ledger.workbench(latest["session_id"])
    return result


def act(webspace: str, action: str, *, session_id: str | None = None, turn_id: str | None = None,
        label: str = "correct", correction: str = "", classification: str | None = None,
        decision: str = "quarantine") -> dict:
    ledger = Ledger()
    sessions = ledger.sessions(scope=webspace+":"+owner())
    selected = next((s for s in sessions if not session_id or s["session_id"] == session_id), None)
    if not selected:
        raise ValueError("session_not_found")
    session_id = selected["session_id"]
    if action == "seal":
        return ledger.seal(session_id)
    turns = [r for r in ledger.records(session_id) if r["kind"] == "turn"]
    if not turn_id and turns:
        turn_id = turns[-1]["turn"]
    if action == "feedback":
        return ledger.feedback(session_id, str(turn_id or ""), label=label, correction=correction, actor=owner())
    if action == "review":
        ledger.bundle(session_id)
        return ledger.review(session_id, str(turn_id or ""), kind="anomaly", actor=owner(),
                             payload={"classification": classification, "decision": decision, "correction": correction})
    raise ValueError("unknown_console_action")
