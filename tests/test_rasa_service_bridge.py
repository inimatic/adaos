from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.anyio
async def test_rasa_service_bridge_reuses_healthy_service_and_emits_detected(monkeypatch):
    from adaos.services.nlu import rasa_service_bridge as bridge

    emitted = []

    class Supervisor:
        async def refresh_discovered(self, force=False):
            return None

        async def start(self, name):
            raise AssertionError("healthy service should not be restarted")

        def resolve_base_url(self, name):
            return "http://127.0.0.1:18092"

    monkeypatch.setattr(bridge, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(bridge, "get_service_supervisor", lambda: Supervisor())
    monkeypatch.setattr(bridge, "ensure_rasa_service_skill_installed", lambda: Path("rasa_nlu_service_skill"))
    monkeypatch.setattr(bridge, "_service_health_ok", lambda base_url: True)
    monkeypatch.setattr(
        bridge,
        "_http_post_json",
        lambda url, payload, *, timeout_ms: {
            "ok": True,
            "result": {
                "intent": {"name": "desktop.open_modal", "confidence": 0.91},
                "entities": [{"entity": "modal_id", "value": "nlu_teacher_modal"}],
            },
        },
    )
    monkeypatch.setattr(bridge, "bus_emit", lambda _bus, event, payload, source=None: emitted.append((event, payload, source)))

    await bridge._parse_and_emit(
        text="открой модалку nlu_teacher_modal",
        webspace_id="ws1",
        request_id="rid1",
        meta={"trace": "test"},
    )

    assert emitted
    event, payload, source = emitted[-1]
    assert event == "nlp.intent.detected"
    assert source == "nlu.rasa"
    assert payload["intent"] == "desktop.open_modal"
    assert payload["slots"] == {"modal_id": "nlu_teacher_modal"}
    assert payload["webspace_id"] == "ws1"
    assert payload["request_id"] == "rid1"
    assert payload["via"] == "rasa"


@pytest.mark.anyio
async def test_rasa_training_bridge_records_successful_training(monkeypatch, tmp_path):
    from adaos.services.nlu import rasa_training_bridge as bridge

    records = []

    class Supervisor:
        async def refresh_discovered(self, force=False):
            return None

        async def start(self, name):
            raise AssertionError("healthy service should not be restarted")

        def resolve_base_url(self, name):
            return "http://127.0.0.1:18092"

    class Workspace:
        def __init__(self, ctx):
            self.ctx = ctx

        def record_training(self, *, note=None, extra=None):
            records.append({"note": note, "extra": extra})
            return {"trained_at": "now"}

    monkeypatch.setenv("ADAOS_NLU_AUTOTRAIN", "1")
    monkeypatch.setattr(bridge, "get_ctx", lambda: SimpleNamespace(paths=SimpleNamespace()))
    monkeypatch.setattr(bridge, "get_service_supervisor", lambda: Supervisor())
    monkeypatch.setattr(bridge, "ensure_rasa_service_skill_installed", lambda: Path("rasa_nlu_service_skill"))
    monkeypatch.setattr(bridge, "_service_health_ok", lambda base_url: True)
    monkeypatch.setattr(bridge, "_train_sync", lambda ctx: {"project_dir": str(tmp_path / "project"), "out_dir": str(tmp_path / "models")})
    monkeypatch.setattr(
        bridge,
        "_http_post_json",
        lambda url, payload, *, timeout_ms=600_000: {"ok": True, "model_path": str(tmp_path / "models" / "interpreter_latest.tar.gz")},
    )
    monkeypatch.setattr(bridge, "InterpreterWorkspace", Workspace)

    await bridge._train_if_enabled("manual")

    assert records == [
        {
            "note": "rasa-auto:manual",
            "extra": {
                "engine": "rasa_service",
                "model_path": str(tmp_path / "models" / "interpreter_latest.tar.gz"),
                "reason": "manual",
            },
        }
    ]
