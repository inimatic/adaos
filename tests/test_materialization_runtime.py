from __future__ import annotations

from types import SimpleNamespace

import pytest

from adaos.services.scenario.webspace_components.materialization_runtime import (
    WebspaceMaterializationService,
)


def test_source_preparation_observation_is_bounded_detached_metadata():
    from adaos.services.scenario.webspace_components.materialization import source_preparation_timings
    source = {"source_stage_timings_ms": {"selected_ui": 14.5, "launcher_metadata": 9,
        "launcher_icons": float("inf"), "shared_ui": -1, "skill_declarations": True,
        "desktop_scenarios": "private text", "private_path": "do-not-export"}}
    assert source_preparation_timings(source) == {
        "prepare_sources.selected_ui": 14.5, "prepare_sources.launcher_metadata": 9}
    assert "private_path" in source["source_stage_timings_ms"]
    assert source_preparation_timings(None) == {}
    assert source_preparation_timings({"source_stage_timings_ms": []}) == {}


@pytest.mark.asyncio
async def test_exact_preview_override_uses_bounded_process_worker() -> None:
    captured: dict[str, object] = {}
    content = {"ui": {"application": {"desktop": {"pageSchema": {"id": "management"}}}}}
    payload = {
        "scenario_id": "web_desktop",
        "application": {"desktop": {"pageSchema": {"id": "management"}}},
    }

    class Resolved:
        def to_registry_entry(self):
            return {"scenario_id": "web_desktop"}

    async def run_worker(webspace_id: str, **kwargs):
        captured.update({"webspace_id": webspace_id, **kwargs})
        return {
            "materialized_payload": payload,
            "rebuild_timings_ms": {"total": 12.0},
            "resolver_debug": {},
            "apply_summary": {"payload_only": True},
            "apply_phase_timings_ms": {},
            "ydoc_timings_ms": {"total": 12.0},
            "worker_parent_elapsed_ms": 12.0,
        }

    def reject_cache(*args, **kwargs):
        raise AssertionError("exact preview overrides must not enter the shared payload cache")

    runtime = SimpleNamespace()
    operations = SimpleNamespace(
        materialization_worker_enabled=lambda: True,
        get_cached_materialized_worker_result=lambda *args, **kwargs: None,
        record_timing=lambda target, name, started: target.update({name: 12.0}),
        run_materialization_worker=run_worker,
        remember_materialized_worker_result=reject_cache,
        raise_if_rebuild_request_superseded=lambda *args, **kwargs: None,
        copy_timing_map=lambda value: dict(value or {}),
        finalize_timing_map=lambda value, **kwargs: dict(value),
        resolved_outputs_from_cache_payload=lambda value: Resolved(),
        materialized_payload_inputs=lambda *args, **kwargs: SimpleNamespace(
            metadata={"materialization": {}},
            compatibility_cache_presence={},
        ),
        coerce_dict=lambda value: dict(value or {}),
        build_materialization_snapshot_from_resolved=lambda **kwargs: {"ready": True},
        describe_webspace_rebuild_state=lambda webspace_id: {"webspace_id": webspace_id},
        normalize_materialization_required_branches=lambda value: [],
        default_materialization_required_branches=("ui.application",),
        set_webspace_rebuild_status_if_current=lambda *args, **kwargs: None,
    )

    result = await WebspaceMaterializationService().resolve_payload(
        runtime,
        operations,
        "desktop-dev",
        request_id="req-1",
        scenario_id="web_desktop",
        scenario_content_override=content,
        skill_source_mode="automation",
    )

    assert result == {"scenario_id": "web_desktop"}
    assert captured["scenario_content_override"] == content
    assert captured["skill_source_mode"] == "automation"
    assert runtime._last_worker_diagnostics["mode"] == "payload_only"
