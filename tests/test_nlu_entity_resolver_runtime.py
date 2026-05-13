from __future__ import annotations

from adaos.services import named_entities
from adaos.services.nlu import entity_resolver_runtime


def _service(records):
    return named_entities.NamedEntityService(
        static_entities=records,
        device_inventory_service=None,
        lookup_payload_provider=lambda **_: {"webspace_id": "desktop", "lookups": {}},
    )


def test_entity_resolver_trace_stage_uses_locale_hints(monkeypatch) -> None:
    service = _service(
        [
            named_entities.NamedEntityRecord(
                canonical_ref="skill:weather_skill",
                kind="skill",
                display_name="Weather",
                labels=(named_entities.EntityLabel(text="Погода", locale="ru", role="alias"),),
                source="test",
                status="confirmed",
            )
        ]
    )
    monkeypatch.setattr(named_entities, "get_named_entity_service", lambda: service)

    stage = entity_resolver_runtime.build_entity_trace_stage(
        {
            "text": "погода",
            "webspace_id": "desktop",
            "request_id": "rid-1",
            "request_locale": "ru",
            "preferred_locales": ["en"],
        }
    )

    assert stage is not None
    assert stage["stage"] == "named_entity"
    assert stage["status"] == "resolved"
    assert stage["raw"]["request_locale"] == "ru"
    assert stage["raw"]["preferred_locales"] == ["ru", "en"]
    assert stage["raw"]["resolved_entities"][0]["canonical_ref"] == "skill:weather_skill"
    assert stage["raw"]["resolved_entities"][0]["locale"] == "ru"
    assert stage["raw"]["model_training"]["rasa_fingerprint"] == "unchanged"


def test_entity_resolver_trace_stage_suppresses_miss_unless_requested(monkeypatch) -> None:
    monkeypatch.setattr(named_entities, "get_named_entity_service", lambda: _service([]))

    payload = {"text": "nothing to resolve", "webspace_id": "desktop", "request_id": "rid-2"}

    assert entity_resolver_runtime.build_entity_trace_stage(payload) is None
    miss = entity_resolver_runtime.build_entity_trace_stage(payload, include_miss=True)
    assert miss is not None
    assert miss["status"] == "miss"
    assert miss["raw"]["resolved_entities"] == []
    assert miss["raw"]["unresolved_entity_spans"] == []
    assert miss["raw"]["normalized_text"] == "nothing to resolve"


def test_entity_resolver_trace_stage_includes_locale_ambiguity_evidence(monkeypatch) -> None:
    service = _service(
        [
            named_entities.NamedEntityRecord(
                canonical_ref="skill:control_a",
                kind="skill",
                display_name="Control A",
                labels=(named_entities.EntityLabel(text="Control", locale="en", role="alias"),),
                source="test",
                status="confirmed",
            ),
            named_entities.NamedEntityRecord(
                canonical_ref="skill:control_b",
                kind="skill",
                display_name="Control B",
                labels=(named_entities.EntityLabel(text="Control", locale="en", role="alias"),),
                source="test",
                status="confirmed",
            ),
        ]
    )
    monkeypatch.setattr(named_entities, "get_named_entity_service", lambda: service)

    stage = entity_resolver_runtime.build_entity_trace_stage(
        {"text": "open Control", "webspace_id": "desktop", "request_id": "rid-3", "request_locale": "en"}
    )

    assert stage is not None
    assert stage["status"] == "ambiguous"
    assert stage["raw"]["ambiguities"][0]["locales"] == ["en"]
    assert {item["canonical_ref"] for item in stage["raw"]["ambiguities"][0]["candidates"]} == {
        "skill:control_a",
        "skill:control_b",
    }
