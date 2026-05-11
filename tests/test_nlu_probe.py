import pytest


@pytest.mark.anyio
async def test_probe_phrase_returns_regex_hit_without_actions():
    from adaos.services.nlu.probe import probe_phrase

    result = await probe_phrase("weather in Berlin", webspace_id="ws-probe", use_rasa=False, emit_trace=False)

    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["via"] == "regex"
    assert result["intent"] == "desktop.open_weather"
    assert result["slots"] == {"city": "Berlin"}
    assert result["intent_ranking"] == [{"name": "desktop.open_weather", "confidence": 1.0}]
    assert [stage["stage"] for stage in result["stages"]] == ["request", "regex"]


@pytest.mark.anyio
async def test_probe_phrase_falls_back_to_rasa_and_keeps_ranking(monkeypatch):
    from adaos.services.nlu import probe as probe_module

    async def _fake_parse_text(text, *, webspace_id=None, request_id=None, meta=None):
        return {
            "ok": True,
            "via": "rasa",
            "intent": "desktop.open_modal",
            "confidence": 0.88,
            "slots": {"modal_id": "nlu_teacher_modal"},
            "entities": [{"entity": "modal_id", "value": "nlu_teacher_modal"}],
            "intent_ranking": [
                {"name": "desktop.open_modal", "confidence": 0.88},
                {"name": "desktop.open_weather", "confidence": 0.04},
            ],
            "raw": {"source": "test"},
        }

    monkeypatch.setattr(probe_module.rasa_service_bridge, "parse_text", _fake_parse_text)

    result = await probe_module.probe_phrase("please show nlu teacher", webspace_id="ws-probe", emit_trace=False)

    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["via"] == "rasa"
    assert result["intent"] == "desktop.open_modal"
    assert result["slots"] == {"modal_id": "nlu_teacher_modal"}
    assert result["intent_ranking"][0]["name"] == "desktop.open_modal"
    assert [(stage["stage"], stage["status"]) for stage in result["stages"]] == [
        ("request", "received"),
        ("regex", "miss"),
        ("rasa", "hit"),
    ]


@pytest.mark.anyio
async def test_nlu_teacher_probe_api_delegates_to_probe_phrase(monkeypatch):
    from adaos.apps.api import nlu_teacher_api as api

    seen = {}

    async def _fake_probe_phrase(text, *, webspace_id=None, use_rasa=True, emit_trace=True):
        seen.update(
            {
                "text": text,
                "webspace_id": webspace_id,
                "use_rasa": use_rasa,
                "emit_trace": emit_trace,
            }
        )
        return {
            "ok": True,
            "accepted": True,
            "text": text.strip(),
            "webspace_id": webspace_id,
            "intent": "desktop.open_modal",
            "intent_ranking": [{"name": "desktop.open_modal", "confidence": 0.9}],
            "entities": [],
            "stages": [],
        }

    monkeypatch.setattr(api, "probe_phrase", _fake_probe_phrase)

    result = await api.probe(
        "ws-api",
        api.ProbePhraseRequest(text=" show nlu teacher ", use_rasa=False, emit_trace=False),
    )

    assert seen == {
        "text": " show nlu teacher ",
        "webspace_id": "ws-api",
        "use_rasa": False,
        "emit_trace": False,
    }
    assert result["ok"] is True
    assert result["intent"] == "desktop.open_modal"
