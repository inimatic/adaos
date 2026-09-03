# tests/test_exporter_descriptions.py
from __future__ import annotations

import json

from adaos.sdk.core.exporter import export as sdk_export
from adaos.services.root_mcp.registry import get_descriptor_set


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
