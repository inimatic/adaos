from pathlib import Path

import pytest
from fastapi import HTTPException

from adaos.services.voice_activation_capture import voice_activation_descriptor


def test_activation_descriptor_uses_installed_agent_aliases(monkeypatch):
    from adaos.apps.api import stt_api
    from adaos.adapters.audio.stt import model_manager
    from adaos.services.router import dialog_registry
    from adaos.services import voice_activation_capture

    monkeypatch.setattr(voice_activation_capture.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(stt_api, "_models_root", lambda: Path("unused"))
    monkeypatch.setattr(model_manager, "resolve_vosk_model", lambda *args: Path("model"))
    monkeypatch.setattr(dialog_registry, "published_agent_records", lambda: [
        {"id": "agent:a", "label": "Арсений", "aliases": ["Сеня", "internal:id"]},
    ])
    result = voice_activation_descriptor("ru")
    assert result["contract"] == "adaos.voice.activation.v1"
    assert result["language"] == "ru-RU"
    assert result["targets"][0]["aliases"] == ["Арсений", "Сеня"]
    monkeypatch.setattr(model_manager, "resolve_vosk_model", lambda *args: None)
    with pytest.raises(HTTPException) as failure:
        voice_activation_descriptor("en-US")
    assert failure.value.status_code == 503

