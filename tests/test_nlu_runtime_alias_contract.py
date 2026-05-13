from __future__ import annotations

import pytest


@pytest.mark.anyio
async def test_runtime_alias_resolves_without_rasa_or_neural_training(monkeypatch) -> None:
    from adaos.services import named_entities
    from adaos.services.nlu import probe as probe_module

    service = named_entities.NamedEntityService(
        static_entities=[
            named_entities.NamedEntityRecord(
                canonical_ref="device:member:node-kitchen",
                kind="device.member",
                display_name="Kitchen Display",
                aliases=("kitchen screen",),
                source="test",
                status="confirmed",
            )
        ],
        device_inventory_service=None,
        lookup_payload_provider=lambda **_: {"webspace_id": "desktop", "lookups": {}},
    )
    monkeypatch.setattr(named_entities, "get_named_entity_service", lambda: service)

    async def _should_not_call_rasa(*args, **kwargs):
        raise AssertionError("runtime alias resolution must not require Rasa parse")

    monkeypatch.setattr(probe_module.rasa_service_bridge, "parse_text", _should_not_call_rasa)

    result = await probe_module.probe_phrase(
        "show logs for kitchen screen",
        webspace_id="desktop",
        use_rasa=False,
        emit_trace=False,
    )

    assert result["ok"] is False
    assert result["reason"] == "rasa_skipped"
    resolution = result["entity_resolution"]
    assert resolution["resolved_entities"][0]["canonical_ref"] == "device:member:node-kitchen"
    assert resolution["resolved_entities"][0]["match_type"] == "alias"
    assert resolution["model_training"] == {
        "rasa_fingerprint": "unchanged",
        "neural_training": "unchanged",
        "reason": "runtime_entity_resolution_only",
    }


def test_runtime_alias_payload_is_not_part_of_rasa_training_fingerprint() -> None:
    from adaos.services.interpreter.workspace import InterpreterWorkspace

    ws = InterpreterWorkspace.__new__(InterpreterWorkspace)
    config = {"intents": [{"name": "desktop.open_modal"}]}
    skills = [{"skill": "browsers_skill", "intents_hash": "h1"}]
    datasets = [{"path": "custom.md", "hash": "d1"}]
    auto_intents = [{"intent": "desktop.open_modal", "examples_hash": "a1"}]
    stable_lookups = [{"lookup": "modal_id", "count": 1, "hash": "l1"}]

    alias_v1 = {"canonical_ref": "device:member:node-kitchen", "aliases": ["kitchen screen"]}
    alias_v2 = {"canonical_ref": "device:member:node-kitchen", "aliases": ["kitchen display"]}

    before = ws.fingerprint(config, skills, datasets, auto_intents, stable_lookups)
    assert alias_v1 != alias_v2
    assert ws.fingerprint(config, skills, datasets, auto_intents, stable_lookups) == before
