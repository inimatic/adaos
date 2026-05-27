from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.anyio
async def test_neuro_lite_parse_text_posts_to_service(monkeypatch):
    from adaos.services.nlu import neuro_lite_service_bridge as bridge

    calls = []
    posted = {}

    class Supervisor:
        async def refresh_discovered(self, force=False):
            calls.append(("refresh", force))

        async def start(self, name):
            calls.append(("start", name))

        def resolve_base_url(self, name):
            calls.append(("resolve", name))
            return "http://127.0.0.1:18093"

    def fake_post(url, payload, *, timeout_ms):
        posted["url"] = url
        posted["payload"] = payload
        posted["timeout_ms"] = timeout_ms
        return {
            "ok": True,
            "result": {
                "accepted": True,
                "top_intent": "voice.timer.start",
                "confidence": 0.42,
                "slots": {"duration": "10 minutes"},
                "model_id": "neuro-lite-unit",
                "evidence": {"backend": "hash_ngram_prototypes"},
            },
        }

    monkeypatch.setattr(bridge, "get_service_supervisor", lambda: Supervisor())
    monkeypatch.setattr(bridge, "_http_post_json", fake_post)

    result = await bridge.parse_text(
        "set timer for 10 minutes",
        webspace_id="desktop",
        request_id="rid-lite",
        locale="ru",
        preferred_locales=["ru", "en"],
    )

    assert result["ok"] is True
    assert result["accepted"] is True
    assert result["intent"] == "voice.timer.start"
    assert result["slots"] == {"duration": "10 minutes"}
    assert posted["url"] == "http://127.0.0.1:18093/parse"
    assert posted["payload"]["request_id"] == "rid-lite"
    assert posted["payload"]["preferred_locales"] == ["ru", "en"]
    assert ("start", "neuro_nlu_lite_skill") in calls


@pytest.mark.anyio
async def test_neuro_lite_event_falls_back_to_neural_on_miss(monkeypatch):
    from adaos.services.nlu import neuro_lite_service_bridge as bridge

    emitted = []

    async def fake_parse_text(*args, **kwargs):
        return {
            "ok": False,
            "accepted": False,
            "reason": "below_margin_threshold",
            "intent": "voice.time.now",
            "confidence": 0.12,
            "slots": {},
            "raw": {"evidence": {"reason": "below_margin_threshold"}},
        }

    async def fake_is_stage_enabled(_webspace_id, stage):
        return stage in {"neuro_lite", "neural"}

    monkeypatch.setattr(bridge, "parse_text", fake_parse_text)
    monkeypatch.setattr(bridge, "is_stage_enabled", fake_is_stage_enabled)
    monkeypatch.setattr(bridge, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(bridge, "bus_emit", lambda _bus, et, payload, source=None: emitted.append((et, payload, source)))

    await bridge._on_nlp_intent_detect_neuro_lite(
        {"text": "what time is it", "webspace_id": "desktop", "request_id": "rid-miss"}
    )

    assert any(
        event_type == "nlu.trace.stage" and payload["stage"] == "neuro_lite" and payload["status"] == "miss"
        for event_type, payload, _ in emitted
    )
    fallback = [payload for event_type, payload, _ in emitted if event_type == "nlp.intent.detect.neural"][0]
    assert fallback["text"] == "what time is it"
    assert fallback["request_id"] == "rid-miss"
    assert fallback["_meta"]["neuro_lite_fallback"] is True
    assert fallback["_meta"]["neuro_lite_fallback_reason"] == "below_margin_threshold"
