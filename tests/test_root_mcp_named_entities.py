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
    assert [item.id for item in plane_contracts] == ["nlu_authoring.get_context"]
    context = result["context"]
    assert context["plane_id"] == "nlu_authoring"
    assert context["locale"]["effective_locale_order"] == ["ru", "en", "und"]
    assert context["canonicalization"]["canonical_ref_required"] is True
    assert context["authoring_boundaries"]["mode"] == "read_only_context"
    assert context["named_entities"]["items"][0]["canonical_ref"] == "skill:weather_skill"
