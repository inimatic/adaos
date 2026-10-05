from concurrent.futures import ThreadPoolExecutor

import pytest

from adaos.services.companion.ledger import Ledger, digest


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_COMPANION_SAGE_ENABLED", "true")
    return Ledger(tmp_path / "evidence.sqlite3")


def start(ledger, identities=None):
    return ledger.start("desktop:user:test", identities or {"model": "test", "regexp": "v1"}, actor="user:test")["session_id"]


def test_session_freeze_seal_and_late_feedback(ledger):
    session = start(ledger)
    assert start(ledger) == session
    ledger.append(session, "turn", {"text": "открой слайд-шоу"}, turn="turn-1")
    failed = ledger.append(session, "tool_result", {"query": "слайд-шоу", "matches": []}, turn="turn-1")
    ledger.append(session, "tool_result", {"query": "slideshow", "matches": ["slideshow"]}, turn="turn-1")
    proposal = ledger.hypothesis(session, turn="turn-1", proposal={"kind": "lexical_alias", "surface": "слайд-шоу", "normalized": "slideshow", "evidence_refs": [failed["id"]]})
    bundle = ledger.seal(session)
    assert bundle["record_count"] == 4
    with pytest.raises(ValueError, match="session_sealed"):
        ledger.append(session, "result", {})
    ledger.feedback(session, "turn-1", label="partial", correction="нужен другой экран", actor="user:test")
    ledger.review(session, proposal["id"], kind="hypothesis", actor="user:test", payload={"status": "quarantined"})
    assert ledger.bundle(session)["digest"] == bundle["digest"]
    assert len(ledger.reviews(session)) == 2
    assert ledger.workbench(session)["cases"][0]["reviews"][0]["payload"]["label"] == "partial"


def test_new_revision_creates_epoch_and_seals_old_session(ledger):
    old = start(ledger)
    new = start(ledger, {"model": "test", "regexp": "v2"})
    assert old != new
    assert ledger.bundle(old)["reason"] == "input_revision_changed"
    with pytest.raises(ValueError, match="sealed_session_required"):
        ledger.bundle(new)


def test_concurrent_evidence_is_ordered_and_idempotent(ledger):
    session = start(ledger)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda i: ledger.append(session, "attempt", {"i": i}, record_id=f"attempt-{i}"), range(20)))
    ledger.verify_records(ledger.records(session))
    assert ledger.append(session, "attempt", {"i": 0}, record_id="attempt-0") == rows[0]
    with pytest.raises(ValueError, match="idempotency_conflict"):
        ledger.append(session, "attempt", {"i": 90}, record_id="attempt-0")
    assert ledger.seal(session)["record_count"] == 20


def test_corrupt_source_evidence_prevents_seal(ledger):
    session = start(ledger)
    ledger.append(session, "attempt", {"error": "missing"})
    with ledger.connect() as db:
        db.execute("UPDATE evidence SET payload='{}'")
    with pytest.raises(ValueError, match="integrity_error"):
        ledger.seal(session)


def test_model_cannot_sign_human_feedback_or_promote(ledger):
    session = start(ledger)
    ledger.append(session, "turn", {"text": "test"}, turn="t")
    with pytest.raises(ValueError, match="human_feedback_required"):
        ledger.feedback(session, "t", label="correct", correction="", actor="model")
    with pytest.raises(ValueError, match="canonical_promotion"):
        ledger.review(session, "t", kind="hypothesis", actor="user:test", payload={"status": "accepted_canonical"})
    with pytest.raises(ValueError, match="feedback_turn_not_found"):
        ledger.feedback(session, "nonexistent", label="correct", correction="", actor="user:test")


def test_bundle_detects_tampering(ledger):
    session = start(ledger)
    ledger.seal(session)
    with ledger.connect() as db:
        db.execute("UPDATE sessions SET bundle_digest='tampered'")
    with pytest.raises(ValueError, match="integrity_error"):
        ledger.bundle(session)
