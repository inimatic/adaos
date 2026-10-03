# tests/test_exporter_descriptions.py
from __future__ import annotations

import json

from adaos.sdk.core.exporter import export as sdk_export
from adaos.sdk.core import exporter as sdk_exporter
from adaos.services.root_mcp.registry import get_descriptor_set
from adaos.services.root_mcp.descriptor_search import get_descriptor_item, search_descriptors


def test_sdk_export_std():
    data = sdk_export(level="std")
    assert "tools" in data and isinstance(data["tools"], list)


def test_sdk_export_mini_lines():
    # mini нужен для LLM ранней стадии
    data = sdk_export(level="mini")
    lines = data.get("__mini_lines__", [])
    # экспортер может возвращать сразу строки или контейнер — проверим оба варианта
    assert isinstance(lines, (list, tuple))


def test_sdk_export_mini_selects_public_quota_sdk_from_task_language():
    data = sdk_export(
        level="mini",
        query=(
            "\u043f\u043e\u043a\u0430\u0436\u0438 \u0440\u0430\u0441\u0445\u043e\u0434 "
            "\u0442\u043e\u043a\u0435\u043d\u043e\u0432 \u0438 \u043e\u0441\u0442\u0430\u0442\u043e\u043a "
            "\u043a\u0432\u043e\u0442\u044b"
        ),
    )

    names = {str(item.get("n") or "") for item in data["items"]}
    assert "adaos.sdk.control_plane.list_quota_objects" in names
    assert len(data["items"]) <= 24


def test_sdk_contract_is_generated_from_one_metadata_source() -> None:
    def sample(limit: int = 10, cursor: str | None = None):
        return None

    contract = sdk_exporter._sdk_contract(
        "manage.sample.list",
        sample,
        {
            "permissions": ["workspace.read"],
            "effects": ["read_only"],
            "errors": ["not_found"],
            "stability": "beta",
            "since": "1.4.0",
            "deprecated": True,
            "removed_in": "2.0.0",
            "replacement": "manage.sample.search",
            "migration_recipe": "Replace list with bounded search.",
        },
    )

    assert contract["permissions"] == ["workspace.read"]
    assert contract["boundedness"]["kind"] == "bounded_page"
    assert contract["pagination"]["supported"] is True
    assert contract["removedIn"] == "2.0.0"
    assert contract["authoring_visibility"] == "migration_only"
    assert contract["digest"].startswith("sha256:")


def test_deprecated_sdk_members_are_hidden_from_authoring_but_visible_to_migration(monkeypatch) -> None:
    item = {
        "kind": "sdk_function",
        "name": "adaos.sdk.example.legacy",
        "module": "adaos.sdk.example",
        "summary": "Legacy API.",
        "meta": {"stability": "deprecated"},
        "contract": {"deprecated": True, "authoring_visibility": "migration_only"},
    }
    monkeypatch.setattr(sdk_exporter, "_filter_tools", lambda: [])
    monkeypatch.setattr(sdk_exporter, "_public_facade_symbols", lambda _level: [item])

    authoring = sdk_exporter.export(
        level="std", query="legacy", include_deprecated=False
    )
    migration = sdk_exporter.export(
        level="std", query="legacy", include_deprecated=True
    )

    assert authoring["tools"] == []
    assert [entry["name"] for entry in migration["tools"]] == [
        "adaos.sdk.example.legacy"
    ]


def test_sdk_metadata_mini_is_a_bounded_nonduplicated_mcp_projection():
    descriptor = get_descriptor_set(
        "sdk_metadata",
        level="mini",
        query="Show token usage and remaining quota",
    )
    payload = descriptor["payload"]

    assert "items" not in payload
    assert payload["overview_rows"]
    assert any(
        row["row_id"] == "adaos.sdk.control_plane.list_quota_objects"
        for row in payload["overview_rows"]
    )
    quota_row = next(
        row
        for row in payload["overview_rows"]
        if row["row_id"] == "adaos.sdk.control_plane.list_quota_objects"
    )
    assert quota_row["metadata"]["args"] == ["webspace_id?"]
    assert not any(
        row["row_id"] == "adaos.sdk.research.apply_projection_patch"
        for row in payload["overview_rows"]
    )
    assert len(json.dumps(descriptor, ensure_ascii=False).encode("utf-8")) < 12_000


def test_sdk_descriptor_drilldown_states_quota_contract_boundary():
    detail = get_descriptor_item(
        "sdk_metadata",
        "adaos.sdk.control_plane.list_quota_objects",
        level="std",
    )

    item = detail["item"]
    description = " ".join(item["description"].split())
    assert "CanonicalObject.to_dict()" in description
    assert "does not return subscription-plan LLM or Codex token usage" in description


def test_sdk_resource_and_persistent_data_contracts_are_discoverable():
    for query, expected in (
        ("skill_data_root", "adaos.sdk.data.skill_env.skill_data_root"),
        ("adaos.sdk.resources.operate", "adaos.sdk.resources.operate"),
        ("adaos.sdk.access.caller", "adaos.sdk.access.caller"),
        ("adaos.sdk.access.invocation", "adaos.sdk.access.invocation"),
        ("adaos.sdk.access.require", "adaos.sdk.access.require"),
        ("adaos.sdk.data.lifecycle.ensure_database", "adaos.sdk.data.lifecycle.ensure_database"),
        ("adaos.sdk.data.blob.put_upload", "adaos.sdk.data.blob.put_upload"),
        ("adaos.sdk.llm.images.generate", "adaos.sdk.llm.images.generate"),
    ):
        result = search_descriptors(query, descriptor_ids=["sdk_metadata"], limit=6)
        assert result["items"][0]["item_id"] == expected
        contract = get_descriptor_item("sdk_metadata", expected)["item"]
        assert contract["name"] == expected
        assert contract["description"] and "signature_detail" in contract


def test_automation_inventory_contract_is_discoverable() -> None:
    expected = "adaos.sdk.automation.inventory"
    result = search_descriptors(expected, descriptor_ids=["sdk_metadata"], limit=6)

    assert result["items"][0]["item_id"] == expected
    contract = get_descriptor_item("sdk_metadata", expected)["item"]
    assert contract["name"] == expected
    assert "bounded fleet snapshot" in contract["description"]


def test_sdk_catalog_is_navigation_not_an_empty_method_contract():
    result = search_descriptors("public SDK skill_data_root", limit=6)
    assert result["items"][0]["item_id"] == "adaos.sdk.data.skill_env.skill_data_root"
    catalog = get_descriptor_item("sdk_metadata", "sdk_metadata")
    assert catalog["item"]["discovery"]["descriptor_ids"] == ["sdk_metadata"]
    assert "payload" not in catalog["item"]


def test_configuration_descriptor_states_runtime_and_secret_boundaries():
    detail = get_descriptor_item("sdk_metadata", "adaos.sdk.data.configuration.read")["item"]
    assert "revision" in detail["description"]
    assert "runtime_scope" in detail["description"]
    assert "development" in detail["description"]
    assert "installed" in detail["description"]
    write = get_descriptor_item("sdk_metadata", "adaos.sdk.data.configuration.write")["item"]
    assert "Credential bindings are preserved" in write["description"]


def test_blob_upload_descriptor_states_binary_and_permission_boundaries():
    detail = get_descriptor_item(
        "sdk_metadata",
        "adaos.sdk.data.blob.put_upload",
    )["item"]
    description = " ".join(detail["description"].split())

    assert "one-use context" in description
    assert "storage.blob" in description
    assert "workspace.write" in description
    assert "workspace.read" in description
    assert "fileStorage: skill" in description
    assert "/api/tools/.../attachments/..." in description
    assert "persist" in description.lower()
    assert detail["signature_detail"]["args"][0]["name"] == "name"
