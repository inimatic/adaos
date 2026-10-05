from __future__ import annotations

import pytest

from adaos.services.companion import policy


@pytest.mark.parametrize("value", [None, "", "false", "0", "1", "yes", "auto", "invalid"])
def test_disabled_gate_rejects_every_entry_before_io(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(policy.ENABLE_FLAG, raising=False)
    else:
        monkeypatch.setenv(policy.ENABLE_FLAG, value)
    from adaos.services.root_mcp import companion_plane, sessions

    assert not policy.enabled()
    assert not policy.agent_available({"id": policy.AGENT_ID})
    assert policy.agent_available({"id": "agent:conversation_companions:arseni"})
    assert companion_plane.contracts() == []
    monkeypatch.setattr(companion_plane, "build_context_frame", lambda **kw: pytest.fail("context I/O"))
    with pytest.raises(PermissionError, match="companion_disabled"):
        companion_plane.handlers()["companion.context.get"]({}, dry_run=False)
    with pytest.raises(PermissionError, match="companion_disabled"):
        sessions.issue_mcp_session_lease(audience="test", actor="test", auth_method="owner_token",
                                       capability_profile="CompanionOperator", target_id="test")
    with pytest.raises(PermissionError, match="companion_disabled"):
        policy.guard_skill_call("conversation_companions", "talk", {"character_id": "sage"})


def test_only_explicit_true_enables_companion(monkeypatch):
    monkeypatch.setenv(policy.ENABLE_FLAG, "true")
    from adaos.services.root_mcp import companion_plane

    assert policy.agent_available({"id": policy.AGENT_ID})
    assert companion_plane.contracts()
    policy.guard_skill_call("conversation_companions", "talk", {"character_id": "sage"})


def test_session_cannot_write_training_or_initiate_builder(monkeypatch):
    monkeypatch.setenv(policy.ENABLE_FLAG, "true")
    for skill in ("builder_skill", "nlu_teacher_skill", "rasa_nlu_service_skill"):
        with pytest.raises(PermissionError, match="session_frozen"):
            policy.guard_skill_call(skill, "train", {"_meta": {"companion_session_id": "session-1"}})


def test_experimental_nlu_cannot_be_invoked():
    for skill in policy.EXPERIMENTAL_NLU:
        with pytest.raises(PermissionError, match="experimental_nlu_removed"):
            policy.guard_skill_call(skill, "parse", {})
