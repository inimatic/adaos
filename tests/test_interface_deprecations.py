from adaos.services.interface_deprecations import (
    deprecation_contract,
    migrate_deprecated_interfaces,
    validate_authoring_interfaces,
)
from adaos.sdk.developer import ui as developer_ui


def _legacy_payload() -> dict:
    return {
        "ui": {"application": {
            "desktop": {"pageSchema": {"id": "demo", "widgets": [
                {"id": "open", "type": "ui.actions", "actions": [
                    {"type": "openModal", "params": {"modalId": "details"}}
                ]}
            ]}},
            "modals": {"details": {"title": "Details", "schema": {}}},
        }}
    }


def test_catalog_drives_generic_migration_and_authoring_admission() -> None:
    rule = deprecation_contract()["deprecated_interfaces"][0]
    assert rule["migrator"] == "open_modal_to_public_view"
    assert validate_authoring_interfaces(_legacy_payload())["ok"] is False

    migrated, receipt = migrate_deprecated_interfaces(_legacy_payload(), owner="demo")

    assert receipt["ok"] is True
    assert receipt["applied"][0]["migrator"] == rule["migrator"]
    assert validate_authoring_interfaces(migrated)["ok"] is True


def test_sdk_exposes_authoring_migration_without_service_imports_for_skills() -> None:
    assert developer_ui.validate_authoring_interfaces(_legacy_payload())["ok"] is False

    migrated, receipt = developer_ui.migrate_deprecated_interfaces(
        _legacy_payload(), owner="demo"
    )

    assert receipt["ok"] is True
    assert developer_ui.validate_authoring_interfaces(migrated)["ok"] is True
