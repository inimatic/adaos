from __future__ import annotations

from adaos.services import named_entities


class _FakeDeviceInventory:
    def __init__(self, devices: list[dict[str, object]]) -> None:
        self._devices = list(devices)

    def list_devices(self, kind=None) -> list[dict[str, object]]:
        return [dict(item) for item in self._devices]


def test_named_entity_display_priority_prefers_registered_over_fallback() -> None:
    record = named_entities.NamedEntityRecord(
        canonical_ref="device:member:node-1",
        kind="device.member",
        registered_names=("Kitchen Display",),
        observed_name="ZVERZVE-A1BNQF7",
        fallback_label="Node 0",
    )

    assert record.display_label == "Kitchen Display"
    assert [label for label, _kind in record.label_candidates()] == [
        "Kitchen Display",
        "ZVERZVE-A1BNQF7",
    ]
    assert [label for label, _kind in record.label_candidates(include_fallback=True)][-1] == "Node 0"


def test_named_entity_service_builds_device_records_from_inventory() -> None:
    service = named_entities.NamedEntityService(
        device_inventory_service=_FakeDeviceInventory(
            [
                {
                    "ref": "member:node-1",
                    "kind": "member",
                    "identity": {
                        "node_id": "node-1",
                        "hostname": "ZVERZVE-A1BNQF7",
                        "node_names": ["Kitchen Display"],
                    },
                    "policy": {"display_name": "", "managed_state": "observed_only"},
                    "observation": {"source": "subnet_directory", "last_seen_at": 100.0},
                    "diagnostics": {"policy_source": "none"},
                }
            ]
        )
    )

    records = service.list_entities()

    assert len(records) == 1
    assert records[0].canonical_ref == "device:member:node-1"
    assert records[0].kind == "device.member"
    assert records[0].display_label == "Kitchen Display"
    assert records[0].observed_name == "ZVERZVE-A1BNQF7"


def test_resolver_matches_exact_labels_without_dispatch_side_effects() -> None:
    service = named_entities.NamedEntityService(
        static_entities=[
            named_entities.NamedEntityRecord(
                canonical_ref="device:member:node-1",
                kind="device.member",
                display_name="Kitchen Display",
                aliases=("kitchen screen",),
            )
        ],
        device_inventory_service=_FakeDeviceInventory([]),
    )

    result = service.resolve_text("show logs for kitchen screen")

    assert result.normalized_text == "show logs for kitchen screen"
    assert [item.canonical_ref for item in result.resolved_entities] == ["device:member:node-1"]
    assert result.resolved_entities[0].match_type == "alias"
    assert result.ambiguities == ()


def test_resolver_reports_ambiguity_instead_of_guessing() -> None:
    service = named_entities.NamedEntityService(
        static_entities=[
            named_entities.NamedEntityRecord(
                canonical_ref="device:member:node-1",
                kind="device.member",
                display_name="Kitchen Display",
                aliases=("screen",),
            ),
            named_entities.NamedEntityRecord(
                canonical_ref="device:browser:browser-1",
                kind="device.browser",
                display_name="Edge on Windows",
                aliases=("screen",),
            ),
        ],
        device_inventory_service=_FakeDeviceInventory([]),
    )

    result = service.resolve_text("open screen settings")

    assert result.resolved_entities == ()
    assert len(result.ambiguities) == 1
    assert result.ambiguities[0].normalized == "screen"
    assert {item.canonical_ref for item in result.ambiguities[0].candidates} == {
        "device:browser:browser-1",
        "device:member:node-1",
    }


def test_sdk_entities_helpers_delegate_to_service(monkeypatch) -> None:
    service = named_entities.NamedEntityService(
        static_entities=[
            named_entities.NamedEntityRecord(
                canonical_ref="webspace:desktop",
                kind="webspace",
                display_name="Desktop",
            )
        ],
        device_inventory_service=_FakeDeviceInventory([]),
    )
    monkeypatch.setattr(named_entities, "get_named_entity_service", lambda: service)

    from adaos.sdk.data import entities as sdk_entities

    assert sdk_entities.list_entities()[0]["canonical_ref"] == "webspace:desktop"
    assert sdk_entities.resolve_text("open Desktop")["resolved_entities"][0]["kind"] == "webspace"
