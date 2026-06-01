from __future__ import annotations

from adaos.services import named_entities
from adaos.services.root_mcp import registry as descriptor_registry
from adaos.services.root_mcp import service as root_mcp_service


class _EmptyDeviceInventory:
    def list_devices(self, kind=None) -> list[dict[str, object]]:
        return []


def _empty_lookup_provider(*, webspace_id: str = "desktop") -> dict[str, object]:
    return {"webspace_id": webspace_id, "lookups": {}}


def test_root_mcp_exposes_named_entity_registry_descriptor(monkeypatch) -> None:
    entity_service = named_entities.NamedEntityService(
        static_entities=[
            named_entities.NamedEntityRecord(
                canonical_ref="skill:browsers_skill",
                kind="skill",
                display_name="Browsers Skill",
                source="test",
                status="confirmed",
            )
        ],
        device_inventory_service=_EmptyDeviceInventory(),
        lookup_payload_provider=_empty_lookup_provider,
    )
    monkeypatch.setattr(named_entities, "get_named_entity_service", lambda: entity_service)

    descriptor = descriptor_registry.get_descriptor_set("named_entity_registry")
    contract = root_mcp_service.get_tool_contract("adaos_dev.get_named_entity_registry")
    result = root_mcp_service._handle_adaos_dev_named_entity_registry(  # type: ignore[attr-defined]
        {"webspace_id": "desktop", "kind": "skill"},
        dry_run=False,
    )

    assert descriptor["payload"]["items"][0]["canonical_ref"] == "skill:browsers_skill"
    assert contract is not None
    assert contract.required_capability == "development.read.descriptors"
    assert result["descriptor"]["payload"]["items"] == descriptor["payload"]["items"]


def test_root_mcp_exposes_nlu_authoring_context_with_named_entities(monkeypatch) -> None:
    entity_service = named_entities.NamedEntityService(
        static_entities=[
            named_entities.NamedEntityRecord(
                canonical_ref="skill:weather_skill",
                kind="skill",
                display_name="Weather",
                aliases=("Погода",),
                source="test",
                status="confirmed",
            )
        ],
        device_inventory_service=_EmptyDeviceInventory(),
        lookup_payload_provider=_empty_lookup_provider,
    )
    monkeypatch.setattr(named_entities, "get_named_entity_service", lambda: entity_service)

    contract = root_mcp_service.get_tool_contract("nlu_authoring.get_context")
    plane_contracts = root_mcp_service.list_tool_contracts(plane_id="nlu_authoring")
    result = root_mcp_service._handle_nlu_authoring_context(  # type: ignore[attr-defined]
        {"webspace_id": "desktop", "kind": "skill", "request_locale": "ru", "preferred_locales": ["en"]},
        dry_run=False,
    )

    assert contract is not None
    assert contract.required_capability == "development.read.descriptors"
    assert contract.metadata["published_by"] == "plane:nlu_authoring"
    assert [item.id for item in plane_contracts] == [
        "nlu_authoring.get_context",
        "nlu_authoring.check_phrase",
        "nlu_authoring.add_device_alias",
        "nlu_authoring.remove_device_alias",
        "nlu_authoring.deprecate_device_alias",
    ]
    context = result["context"]
    assert context["plane_id"] == "nlu_authoring"
    assert context["locale"]["effective_locale_order"] == ["ru", "en", "und"]
    assert context["canonicalization"]["canonical_ref_required"] is True
    assert context["authoring_boundaries"]["mode"] == "read_only_context"
    assert context["named_entities"]["items"][0]["canonical_ref"] == "skill:weather_skill"


def test_root_mcp_exposes_nlu_authoring_phrase_check(monkeypatch) -> None:
    async def _fake_probe_phrase(
        text,
        *,
        webspace_id=None,
        use_rasa=True,
        emit_trace=True,
        request_locale=None,
        preferred_locales=None,
    ):
        return {
            "ok": True,
            "accepted": True,
            "text": text,
            "webspace_id": webspace_id,
            "intent": "desktop.open_weather",
            "via": "regex",
            "slots": {"city": "Berlin"},
            "request_locale": request_locale,
            "preferred_locales": preferred_locales,
            "use_rasa": use_rasa,
            "emit_trace": emit_trace,
        }

    from adaos.services.nlu import probe as probe_module

    monkeypatch.setattr(probe_module, "probe_phrase", _fake_probe_phrase)

    contract = root_mcp_service.get_tool_contract("nlu_authoring.check_phrase")
    result = root_mcp_service._handle_nlu_authoring_check_phrase(  # type: ignore[attr-defined]
        {
            "text": "weather in Berlin",
            "webspace_id": "desktop",
            "use_rasa": False,
            "emit_trace": False,
            "request_locale": "en",
            "preferred_locales": ["ru"],
        },
        dry_run=False,
    )

    assert contract is not None
    assert contract.required_capability == "development.read.descriptors"
    assert contract.side_effects == "trace_optional"
    assert contract.metadata["published_by"] == "plane:nlu_authoring"
    assert result["check"]["ok"] is True
    assert result["check"]["intent"] == "desktop.open_weather"
    assert result["check"]["use_rasa"] is False
    assert result["check"]["request_locale"] == "en"
    assert result["authoring_boundaries"]["dispatch"] is False
    assert result["authoring_boundaries"]["training_mutation"] is False

    dry_result = root_mcp_service._handle_nlu_authoring_check_phrase(  # type: ignore[attr-defined]
        {"text": "weather in Berlin", "emit_trace": True},
        dry_run=True,
    )
    assert dry_result["check"]["emit_trace"] is False
    assert dry_result["authoring_boundaries"]["side_effects"] == "none"


def test_root_mcp_exposes_governed_device_alias_write(monkeypatch) -> None:
    entity_service = named_entities.NamedEntityService(
        static_entities=[
            named_entities.NamedEntityRecord(
                canonical_ref="device:browser:browser-1",
                kind="device.browser",
                display_name="Work browser",
            )
        ],
        device_inventory_service=_EmptyDeviceInventory(),
        lookup_payload_provider=_empty_lookup_provider,
    )
    monkeypatch.setattr(named_entities, "get_named_entity_service", lambda: entity_service)
    calls: list[dict[str, object]] = []

    def _fake_add_device_alias(device_ref, alias, *, locale=None, actor=None, request_id=None, base_fingerprint=None):
        calls.append(
            {
                "device_ref": device_ref,
                "alias": alias,
                "locale": locale,
                "actor": actor,
                "request_id": request_id,
            }
        )
        return {"ok": True, "status": "applied", "device_ref": device_ref}

    from adaos.sdk.data import entities as sdk_entities

    monkeypatch.setattr(sdk_entities, "add_device_alias", _fake_add_device_alias)

    contract = root_mcp_service.get_tool_contract("nlu_authoring.add_device_alias")
    dry_run = root_mcp_service._handle_nlu_authoring_add_device_alias(  # type: ignore[attr-defined]
        {"device_ref": "browser:browser-1", "alias": "office browser", "locale": "en"},
        dry_run=True,
    )
    applied = root_mcp_service._handle_nlu_authoring_add_device_alias(  # type: ignore[attr-defined]
        {"device_ref": "browser:browser-1", "alias": "office browser", "locale": "en", "request_id": "req-1"},
        dry_run=False,
    )

    assert contract is not None
    assert contract.required_capability == "development.write.named_entities"
    assert contract.side_effects == "write"
    assert dry_run["ok"] is True
    assert dry_run["status"] == "proposed"
    assert dry_run["side_effects"] == "none"
    assert applied["ok"] is True
    assert applied["status"] == "applied"
    assert calls == [
        {
            "device_ref": "browser:browser-1",
            "alias": "office browser",
            "locale": "en",
            "actor": "root_mcp:nlu_authoring",
            "request_id": "req-1",
        }
    ]


def test_root_mcp_device_alias_write_emits_domain_audit(monkeypatch) -> None:
    captured: list[object] = []

    def _fake_add_device_alias(device_ref, alias, *, locale=None, actor=None, request_id=None, base_fingerprint=None):
        return {
            "ok": True,
            "status": "applied",
            "device_ref": device_ref,
            "proposal": {
                "canonical_ref": "device:browser:browser-1",
                "entity_kind": "device.browser",
                "alias": alias,
                "locale": locale or "und",
                "base_fingerprint": base_fingerprint,
                "reason": "alias_available",
            },
            "updated_record": {"fingerprint": "fp-2"},
            "events": [{"topic": named_entities.ENTITY_ALIAS_ADDED, "payload": {}}],
        }

    from adaos.sdk.data import entities as sdk_entities

    monkeypatch.setattr(sdk_entities, "add_device_alias", _fake_add_device_alias)
    monkeypatch.setattr(root_mcp_service, "append_audit_event", lambda event: captured.append(event) or event)

    response = root_mcp_service.invoke_tool(
        "nlu_authoring.add_device_alias",
        arguments={
            "device_ref": "browser:browser-1",
            "alias": "office browser",
            "locale": "en",
            "base_fingerprint": "fp-1",
        },
        request_id="req-root",
        trace_id="trace-root",
        actor="codex",
        auth_method="bearer",
        auth_context={"capabilities": ["development.write.named_entities"]},
    )

    domain_events = [item for item in captured if getattr(item, "tool_id", "") == "entity.alias.add"]
    assert response.ok is True
    assert domain_events
    domain = domain_events[0]
    assert domain.request_id == "req-root"
    assert domain.trace_id == "trace-root"
    assert domain.actor == "codex"
    assert domain.capability == "development.write.named_entities"
    assert domain.result_summary["canonical_ref"] == "device:browser:browser-1"
    assert domain.result_summary["alias"] == "office browser"
    assert domain.result_summary["base_fingerprint"] == "fp-1"
    assert domain.result_summary["entry_fingerprint"] == "fp-2"


def test_root_mcp_exposes_governed_device_alias_remove_and_deprecate(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    def _fake_remove_device_alias(device_ref, alias, *, locale=None, actor=None, request_id=None, base_fingerprint=None):
        calls.append(("remove", device_ref, alias))
        return {
            "ok": True,
            "status": "applied",
            "device_ref": device_ref,
            "proposal": {
                "action": "alias.remove",
                "canonical_ref": "device:browser:browser-1",
                "entity_kind": "device.browser",
                "alias": alias,
                "locale": locale or "und",
                "base_fingerprint": base_fingerprint,
                "reason": "alias_registered",
            },
            "updated_record": {"fingerprint": "fp-3"},
            "events": [{"topic": named_entities.ENTITY_ALIAS_REMOVED, "payload": {}}],
        }

    def _fake_deprecate_device_alias(device_ref, alias, *, locale=None, actor=None, request_id=None, base_fingerprint=None):
        calls.append(("deprecate", device_ref, alias))
        return {
            "ok": True,
            "status": "applied",
            "device_ref": device_ref,
            "proposal": {
                "action": "alias.deprecate",
                "canonical_ref": "device:browser:browser-1",
                "entity_kind": "device.browser",
                "alias": alias,
                "locale": locale or "und",
                "base_fingerprint": base_fingerprint,
                "reason": "alias_registered",
            },
            "updated_record": {"fingerprint": "fp-4"},
            "events": [{"topic": named_entities.ENTITY_ALIAS_DEPRECATED, "payload": {}}],
        }

    from adaos.sdk.data import entities as sdk_entities

    monkeypatch.setattr(sdk_entities, "remove_device_alias", _fake_remove_device_alias)
    monkeypatch.setattr(sdk_entities, "deprecate_device_alias", _fake_deprecate_device_alias)
    monkeypatch.setattr(root_mcp_service, "append_audit_event", lambda event: event)

    removed = root_mcp_service._handle_nlu_authoring_remove_device_alias(  # type: ignore[attr-defined]
        {"device_ref": "browser:browser-1", "alias": "office browser", "locale": "en"},
        dry_run=False,
    )
    deprecated = root_mcp_service._handle_nlu_authoring_deprecate_device_alias(  # type: ignore[attr-defined]
        {"device_ref": "browser:browser-1", "alias": "old browser", "locale": "en"},
        dry_run=False,
    )

    assert root_mcp_service.get_tool_contract("nlu_authoring.remove_device_alias") is not None
    assert root_mcp_service.get_tool_contract("nlu_authoring.deprecate_device_alias") is not None
    assert removed["ok"] is True
    assert removed["result"]["proposal"]["action"] == "alias.remove"
    assert deprecated["ok"] is True
    assert deprecated["result"]["proposal"]["action"] == "alias.deprecate"
    assert calls == [
        ("remove", "browser:browser-1", "office browser"),
        ("deprecate", "browser:browser-1", "old browser"),
    ]
