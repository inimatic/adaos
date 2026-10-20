import pytest
from adaos.services import voice_runtime, voice_native_dialogue as native


@pytest.fixture
def voice_state(tmp_path, monkeypatch):
    from adaos.services.router import dialog_registry
    monkeypatch.setattr(voice_runtime, "long_form_sessions_path", lambda: tmp_path / "long.json")
    monkeypatch.setattr(dialog_registry, "published_agent_records", lambda: [{"id": "a", "label": "Арсений"}])
    return tmp_path


def test_long_question_is_one_dispatch_and_cancellation_is_not_commit(voice_state):
    seq = 0
    def call(action, text="", **kwargs):
        nonlocal seq
        seq += 1
        return native.dialogue_command("chat", "phone", {"action": action, "target_id": "a", "text": text, "operation_id": str(seq)}, **kwargs)
    call("start")
    call("listen")
    for i in range(600):
        result = call("append", f"part{i}")
        assert "dispatch_text" not in result
    assert "part0 " in result["draft"]
    final = call("commit")
    assert final["user_text"].startswith("part0 ") and final["user_text"].endswith("part599")
    assert final["mode"] == "dialogue"
    duplicate = native.dialogue_command("chat", "phone", {"action": "commit", "operation_id": str(seq)})
    assert duplicate["duplicate"] and "dispatch_text" not in duplicate
    call("listen"); call("append", "private draft")
    assert "dispatch_text" not in call("cancel")
    assert "dispatch_text" not in call("end")
    with pytest.raises(ValueError, match="start_required"):
        call("turn", "ambient")


def test_endpoint_lease_and_no_idle_dialogue_timeout(voice_state):
    native.dialogue_command("chat", "phone", {"action": "start", "target_id": "a", "operation_id": "1"}, now=100)
    with pytest.raises(ValueError, match="another_endpoint"):
        native.dialogue_command("chat", "pc", {"action": "start", "target_id": "a", "operation_id": "2"}, now=110)
    result = native.dialogue_command("chat", "phone", {"action": "turn", "text": "question", "operation_id": "3"}, now=10000)
    assert result["dispatch_text"] == "Арсений, question"


def test_draft_limit_preserves_previous_text(voice_state):
    for action, text, operation in [("start", "", "1"), ("listen", "", "2"), ("append", "A" * 49998, "3")]:
        native.dialogue_command("chat", "phone", {"action": action, "target_id": "a", "text": text, "operation_id": operation})
    with pytest.raises(ValueError, match="draft_limit"):
        native.dialogue_command("chat", "phone", {"action": "append", "text": "overflow", "operation_id": "4"})
    scope=voice_runtime.long_form_scope_key("chat",device_id="native-dialogue:phone")
    assert voice_runtime.read_long_form_session(scope)["text"] == "A" * 49998
