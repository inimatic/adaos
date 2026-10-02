from __future__ import annotations

from adaos.services.root_mcp.model import RootMcpSurface, RootMcpToolContract
from adaos.services.root_mcp import descriptor_search
from adaos.services.root_mcp import service as root_mcp_service
from adaos.services.root_mcp.output_profile import (
    audit_search_contracts,
    measure_output,
    profile_audit_events,
)


def _contract(tool_id: str, properties: dict) -> RootMcpToolContract:
    return RootMcpToolContract(
        id=tool_id,
        title=tool_id,
        surface=RootMcpSurface.DEVELOPMENT,
        summary="Search a bounded catalog",
        input_schema={
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        },
    )


def test_measure_output_counts_characters_bytes_and_emits_signal() -> None:
    measured = measure_output(
        {"message": "Привет"}, review_chars=5, optimize_chars=10
    )

    assert measured["serialized_bytes"] > measured["serialized_chars"]
    assert measured["estimated_tokens"] > 0
    assert measured["output_digest"].startswith("sha256:")
    assert measured["optimization_signal"] == "optimize"


def test_profile_audit_events_returns_largest_tools_first() -> None:
    events = [
        {
            "tool_id": "small.search",
            "result_summary": {
                "serialized_chars": 10,
                "serialized_bytes": 10,
                "optimization_signal": "ok",
            },
        },
        {
            "tool_id": "large.search",
            "result_summary": {
                "serialized_chars": 30_000,
                "serialized_bytes": 31_000,
                "optimization_signal": "review",
            },
        },
        {"tool_id": "large.search", "result_summary": {"kind": "object"}},
    ]

    profile = profile_audit_events(events, top_n=1)

    assert profile["event_count"] == 3
    assert profile["measured_event_count"] == 2
    assert profile["top"][0]["tool_id"] == "large.search"
    assert profile["top"][0]["calls"] == 2
    assert profile["top"][0]["signals"]["review"] == 1
    assert profile["top"][0]["latest_chars"] == 30_000


def test_search_contract_audit_requires_bound_and_pagination() -> None:
    audit = audit_search_contracts(
        [
            _contract(
                "healthy.search",
                {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "maximum": 64},
                    "cursor": {"type": "string"},
                },
            ),
            _contract("legacy.query", {"query": {"type": "string"}}),
            _contract("detail.get_query_result", {}),
        ]
    )

    by_id = {item["tool_id"]: item for item in audit["items"]}
    assert by_id["healthy.search"]["status"] == "ok"
    assert by_id["legacy.query"]["signals"] == [
        "missing_top_k",
        "missing_pagination",
    ]
    assert "detail.get_query_result" not in by_id


def test_descriptor_search_cursor_returns_the_next_compact_page(monkeypatch) -> None:
    monkeypatch.setattr(
        descriptor_search,
        "list_descriptor_sets",
        lambda: [
            {
                "descriptor_id": "sdk_metadata",
                "title": "SDK",
                "summary": "SDK tools",
                "descriptor_class": "catalog",
                "stability": "beta",
            }
        ],
    )
    monkeypatch.setattr(
        descriptor_search,
        "get_descriptor_set",
        lambda *args, **kwargs: {
            "payload": {
                "overview_rows": [
                    {
                        "row_id": f"tool.{index}",
                        "kind": "sdk_function",
                        "title": f"Tool {index}",
                        "summary": "useful tool",
                    }
                    for index in range(3)
                ]
            }
        },
    )

    first = descriptor_search.search_descriptors(
        "tool", descriptor_ids=["sdk_metadata"], limit=1
    )
    second = descriptor_search.search_descriptors(
        "tool",
        descriptor_ids=["sdk_metadata"],
        limit=1,
        cursor=first["next_cursor"],
    )

    assert first["has_more"] is True
    assert first["offset"] == 0
    assert second["offset"] == 1
    assert first["items"][0]["item_id"] != second["items"][0]["item_id"]


def test_large_root_catalog_lists_are_bounded_and_continuable() -> None:
    contracts = root_mcp_service._handle_list_contracts(  # type: ignore[attr-defined]
        {"limit": 1}, dry_run=False
    )
    descriptors = root_mcp_service._handle_list_descriptor_sets(  # type: ignore[attr-defined]
        {"limit": 1}, dry_run=False
    )

    assert contracts["count"] == 1
    assert contracts["total_count"] > 1
    assert contracts["has_more"] is True
    assert contracts["next_offset"] == 1
    assert descriptors["count"] == 1
    assert descriptors["total_count"] > 1
    assert descriptors["has_more"] is True
    assert descriptors["next_offset"] == 1
