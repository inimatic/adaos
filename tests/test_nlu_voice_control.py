from __future__ import annotations

from adaos.services.nlu import voice_control


def test_voice_control_gate_keeps_only_high_confidence_control_intents(monkeypatch) -> None:
    replies = {
        "Арсений, послушай вопрос": ("conversation.talk", 0.94),
        "послушай вопрос": ("voice.long_form.dialog.start", 0.88),
    }

    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

        def json(self):
            name, confidence = replies[self.text]
            return {
                "ok": True,
                "result": {"intent": {"name": name, "confidence": confidence}},
            }

    def _post(_url: str, *, json: dict, timeout: float):
        assert timeout == 0.35
        return _Response(json["text"])

    monkeypatch.setattr(voice_control.requests, "post", _post)

    result = voice_control.detect_voice_control_intent(replies)

    assert result == {
        "intent": "voice.long_form.dialog.start",
        "confidence": 0.88,
        "text": "послушай вопрос",
        "provider": "rasa",
        "mode": "voice_control_gate",
    }
