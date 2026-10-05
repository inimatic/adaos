import pytest

from adaos.services.companion import console
from adaos.services.companion.ledger import Ledger


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_COMPANION_SAGE_ENABLED", "true")
    ledger = Ledger(tmp_path / "lab.sqlite3")
    monkeypatch.setattr(console, "Ledger", lambda: ledger)
    monkeypatch.setattr(console, "owner", lambda: "user:owner")
    monkeypatch.setattr(console, "evidence_webspace", lambda ws: "source" if ws == "preview" else ws)
    session = ledger.start("source:user:owner", {}, actor="user:owner")["session_id"]
    ledger.append(session, "turn", {"text": "Hello"}, turn="t")
    ledger.append(session, "response", {"message": "Hello", "duration_ms": 10}, turn="t")
    return ledger, session


def test_preview_console_reviews_source_session_without_rewriting_evidence(lab):
    ledger, session = lab
    state = console.snapshot("preview")
    assert state["webspace_id"] == "source" and state["session_id"] == session
    assert state["turn_id"] == "t" and state["items"][0]["title"] == "Hello"
    sealed = console.act("preview", "seal")
    console.act("preview", "feedback", label="partial", correction="qualification fixture")
    console.act("preview", "review", classification="context_projection_miss", decision="backlog")
    state = console.snapshot("preview")
    assert state["state"] == "sealed"
    assert len(state["reviews"]) == 2
    assert ledger.bundle(session)["digest"] == sealed["digest"]


def test_console_cannot_select_other_owner_session(lab):
    ledger, _ = lab
    other = ledger.start("source:user:other", {}, actor="user:other")["session_id"]
    with pytest.raises(ValueError, match="session_not_found"):
        console.act("preview", "seal", session_id=other)


def test_console_source_uses_authoritative_builder_relation(monkeypatch):
    from adaos.services.builder.workbench import BuilderWorkbenchService

    seen = []
    def resolve(self, target):
        seen.append(target)
        return "bound-source"
    monkeypatch.setattr(BuilderWorkbenchService, "resolve_source_webspace_id", resolve)
    assert console.evidence_webspace("not-a-dev-suffix") == "bound-source"
    assert seen == ["not-a-dev-suffix"]
