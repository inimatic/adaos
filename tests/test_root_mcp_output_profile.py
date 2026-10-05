from __future__ import annotations

import pytest

from adaos.services.root_mcp.model import RootMcpSurface, RootMcpToolContract
from adaos.services.root_mcp import descriptor_search
from adaos.services.root_mcp import service as root_mcp_service
from adaos.services.root_mcp.output_profile import (
    audit_search_contracts,
    context_curator_decision,
    context_pressure_stage,
    evaluate_release_slo,
    evaluate_source_budget_change,
    measure_output,
    profile_audit_events,
)
from adaos.services.root_mcp.opaque_cursor import (
    decode_opaque_cursor,
    encode_opaque_cursor,
)
from adaos.services.root_mcp import registry as descriptor_registry


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
                "estimated_tokens": 3,
                "latency_ms": 5.0,
                "cache_hit": True,
                "optimization_signal": "ok",
            },
        },
        {
            "tool_id": "large.search",
            "result_summary": {
                "serialized_chars": 30_000,
                "serialized_bytes": 31_000,
                "estimated_tokens": 7_750,
                "latency_ms": 80.0,
                "cache_hit": False,
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
    assert profile["top"][0]["p95_tokens"] == 7_750
    assert profile["top"][0]["p50_latency_ms"] == 80.0
    assert profile["top"][0]["cache_hit_rate"] == 0.0


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


def test_collection_contract_audit_blocks_a_new_unbounded_list() -> None:
    audit = audit_search_contracts([_contract("new_surface.list_records", {})])

    assert audit["blocking_count"] == 1
    assert audit["items"][0]["signals"] == [
        "missing_top_k",
        "missing_pagination",
    ]


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


def test_public_registry_is_a_compact_cursor_index_with_separate_detail(monkeypatch) -> None:
    source = [
        {
            "id": "alpha_skill",
            "version": "1.2.3",
            "stability": "stable",
            "description": "Alpha tools",
            "manifest_payload": {
                "capabilities": ["workspace.read"],
                "tools": [
                    {
                        "name": "read_alpha",
                        "description": "Read the alpha record.",
                        "side_effects": "read_only",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                        "permissions": ["workspace.read"],
                        "application_access": {
                            "permission": "workspace.read",
                            "capability": "alpha.read",
                        },
                        "examples": [{"value": "alpha"}],
                    }
                ],
            },
        },
        {"id": "beta_skill", "version": "2.0.0", "stability": "beta"},
    ]
    monkeypatch.setattr(descriptor_registry, "_registry_entries", lambda _kind: source)
    monkeypatch.setattr(
        descriptor_registry,
        "_registry_manifest",
        lambda item: dict(item.get("manifest_payload") or {}),
    )

    first = descriptor_registry._public_registry_summary("skills", limit=1)
    second = descriptor_registry._public_registry_summary(
        "skills", limit=1, cursor=first["next_cursor"]
    )
    detail = descriptor_registry.public_registry_item("skills", "alpha_skill")

    assert set(first["items"][0]) == {
        "id",
        "version",
        "stability",
        "capabilities",
        "digest",
    }
    assert first["has_more"] is True
    assert second["offset"] == 1
    assert detail["schemas"]["read_alpha"]["input"] == {"type": "object"}
    assert detail["examples"]["read_alpha"] == [{"value": "alpha"}]
    assert detail["tool_contracts"]["read_alpha"] == {
        "description": "Read the alpha record.",
        "side_effects": "read_only",
        "permissions": ["workspace.read"],
        "application_access": {
            "permission": "workspace.read",
            "capability": "alpha.read",
        },
        "approval_scope": None,
    }


def test_descriptor_delta_returns_only_changed_and_removed_fragments() -> None:
    delta = descriptor_registry._descriptor_delta(
        {"items": [{"id": "same", "value": 1}, {"id": "removed", "value": 2}]},
        {"items": [{"id": "same", "value": 3}, {"id": "added", "value": 4}]},
        base_etag="sha256:old",
        etag="sha256:new",
    )

    assert {item["fragment_id"] for item in delta["changed"]} == {
        "items:added",
        "items:same",
    }
    assert delta["removed"] == ["items:removed"]


def test_architecture_catalog_is_a_bounded_typed_graph() -> None:
    graph = descriptor_registry._architecture_catalog(limit=1)

    assert graph["schema"] == "adaos.architecture.graph.v1"
    assert len(graph["nodes"]) <= 1
    page_ids = {item["id"] for item in graph["nodes"]}
    assert all(
        edge["source"] in page_ids and edge["target"] in page_ids
        for edge in graph["edges"]
    )


def test_opaque_cursor_is_bound_to_query_field_mask_digest_and_signature() -> None:
    cursor = encode_opaque_cursor(
        namespace="test",
        offset=3,
        query={"q": "router"},
        field_mask=["id", "title"],
        content_digest="sha256:generation-1",
    )

    assert decode_opaque_cursor(
        cursor,
        namespace="test",
        query={"q": "router"},
        field_mask=["id", "title"],
        content_digest="sha256:generation-1",
    ) == 3
    for overrides in (
        {"query": {"q": "other"}},
        {"field_mask": ["id"]},
        {"content_digest": "sha256:generation-2"},
    ):
        arguments = {
            "namespace": "test",
            "query": {"q": "router"},
            "field_mask": ["id", "title"],
            "content_digest": "sha256:generation-1",
            **overrides,
        }
        with pytest.raises(ValueError):
            decode_opaque_cursor(cursor, **arguments)
    replacement = "A" if cursor[-1] != "A" else "B"
    with pytest.raises(ValueError):
        decode_opaque_cursor(
            cursor[:-1] + replacement,
            namespace="test",
            query={"q": "router"},
            field_mask=["id", "title"],
            content_digest="sha256:generation-1",
        )


def test_architecture_catalog_caches_only_bounded_neighborhoods() -> None:
    descriptor_registry._ARCHITECTURE_NEIGHBORHOOD_CACHE.clear()

    first = descriptor_registry._architecture_catalog(query="router", limit=2, depth=1)
    second = descriptor_registry._architecture_catalog(query="router", limit=2, depth=1)
    unrooted = descriptor_registry._architecture_catalog(limit=1)

    assert len(first["nodes"]) <= 2
    assert first["cache"]["scope"] == "graph_neighborhood"
    assert first["cache"]["hit"] is False
    assert second["cache"]["hit"] is True
    assert unrooted["cache"]["scope"] == "none"


def test_context_margin_stages_preserve_reserve() -> None:
    assert context_pressure_stage(used_tokens=700, capacity_tokens=1000)["stage"] == "observe"
    assert context_pressure_stage(used_tokens=850, capacity_tokens=1000)["stage"] == "deterministic_trim"
    assert context_pressure_stage(used_tokens=920, capacity_tokens=1000)["stage"] == "model_compact"
    reserve = context_pressure_stage(used_tokens=970, capacity_tokens=1000)
    assert reserve["stage"] == "reserve"
    assert "deny_new_tool_call" in reserve["actions"]


def test_p95_source_budget_changes_require_independent_quality_gate() -> None:
    denied_raise = evaluate_source_budget_change(
        source_id="registry",
        current_budget=1000,
        observed_p95=1400,
        baseline_quality=0.91,
        candidate_quality=0.911,
        quality_floor=0.90,
    )
    accepted_trim = evaluate_source_budget_change(
        source_id="registry",
        current_budget=1000,
        observed_p95=800,
        baseline_quality=0.91,
        candidate_quality=0.91,
        quality_floor=0.90,
    )

    assert denied_raise["basis"] == "observed_p95"
    assert denied_raise["approved"] is False
    assert denied_raise["effective_budget"] == 1000
    assert accepted_trim["approved"] is True
    assert accepted_trim["effective_budget"] == 800


def test_release_slo_includes_recall_as_an_independent_gate() -> None:
    result = evaluate_release_slo(
        [
            {
                "tool_id": "registry.search",
                "p95_chars": 8000,
                "p95_tokens": 2000,
                "p95_latency_ms": 80,
                "cache_hit_rate": 0.9,
            }
        ],
        p95_chars_max=10_000,
        p95_tokens_max=2500,
        p95_latency_ms_max=100,
        cache_hit_rate_min=0.8,
        must_keep_recall={"registry.search": 0.94},
        must_keep_recall_min=0.95,
    )

    assert result["passed"] is False
    assert result["items"][0]["violations"] == ["must_keep_recall_below_slo"]


def test_context_curator_is_rare_and_adopts_only_evaluated_algorithmic_recipe() -> None:
    too_early = context_curator_decision(
        source_id="sdk",
        calls_since_last_run=5,
        expected_saved_tokens=100,
        estimated_run_cost_tokens=100,
        algorithmic_recipe={"field_mask": ["id"]},
        eval_passed=True,
    )
    learned = context_curator_decision(
        source_id="sdk",
        calls_since_last_run=20,
        expected_saved_tokens=1000,
        estimated_run_cost_tokens=500,
        algorithmic_recipe={"field_mask": ["id"]},
        eval_passed=True,
    )

    assert too_early["admitted"] is False
    assert learned["mode"] == "control_plane_learning"
    assert learned["adopt_recipe"] is True
    assert learned["contract_patch"] == {"field_mask": ["id"]}


def test_legacy_collection_cursor_rejects_changed_query_and_source() -> None:
    values = [{"id": str(index)} for index in range(6)]
    arguments = {"search": "notes", "limit": 2}
    page = root_mcp_service._bounded_collection_result(
        "dev_ticket.list", arguments, {"tickets": values}, offset=0, limit=2,
    )
    continued = {**arguments, "cursor": page["page"]["next_cursor"]}
    assert root_mcp_service._collection_offset("dev_ticket.list", continued) == 2
    second = root_mcp_service._bounded_collection_result(
        "dev_ticket.list", continued, {"tickets": values}, offset=2, limit=2,
    )
    assert second["tickets"] == values[2:4]
    with pytest.raises(ValueError):
        root_mcp_service._collection_offset("dev_ticket.list", {**continued, "search": "weather"})
    with pytest.raises(ValueError):
        root_mcp_service._bounded_collection_result(
            "dev_ticket.list", continued, {"tickets": [{"id": "changed"}, *values[1:]]}, offset=2, limit=2,
        )


def test_ci_profiler_always_samples_and_rejects_empty_measurements(tmp_path, monkeypatch) -> None:
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "mcp_profiler_under_test", Path(__file__).resolve().parents[1] / "tools/mcp_output_profiler.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(module, "init_ctx", lambda: None)
    monkeypatch.setattr(module, "configure_default_distributed_runtimes", lambda *a, **kw: None)
    monkeypatch.setattr(module, "list_tool_contracts", lambda: [])
    monkeypatch.setattr(module, "list_audit_events", lambda **kw: [])
    calls = []
    monkeypatch.setattr(module, "_sample_safe_tools", lambda: calls.append(True) or [])
    monkeypatch.setattr(module.sys, "argv", ["profiler", "--ci", "--output", str(tmp_path / ".tmp/report.json")])
    assert module.main() == 1
    assert calls == [True]
