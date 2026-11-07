from __future__ import annotations

from types import SimpleNamespace
from contextlib import asynccontextmanager
import asyncio
import threading

import y_py as Y

import pytest

from adaos.services.scenario.webspace_components.materialization_runtime import (
    WebspaceMaterializationService,
)


@pytest.mark.parametrize(
    "mode", ["resolve_payload", "rebuild", "chained_rebuild", "grouped_rebuild"]
)
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("cross_thread", [False, True])
@pytest.mark.asyncio
async def test_materialization_failure_does_not_export_native_frame_locals(
    mode, cancelled, cross_thread
):
    error = (
        asyncio.CancelledError("controlled cancellation")
        if cancelled
        else ValueError("controlled source failure")
    )

    owners = []

    @asynccontextmanager
    async def doc_session(*args, **kwargs):
        owners.append(threading.get_ident())
        doc = Y.YDoc()
        try:
            yield doc
        finally:
            doc = None

    async def run(function, *args, **kwargs):
        return function(*args, **kwargs)

    def fail_sources(*args, **kwargs):
        raise error

    async def fail_rebuild(ydoc, *args, **kwargs):
        ui_map = ydoc.get_map("ui")

        def retain_in_cell():
            return ydoc

        assert retain_in_cell() is ydoc
        assert ui_map is not None
        if mode in {"chained_rebuild", "grouped_rebuild"}:

            async def cause_native(doc):
                cause_map = doc.get_map("ui")
                assert cause_map is not None
                raise RuntimeError("controlled native cause")

            try:
                await cause_native(ydoc)
            except RuntimeError as cause:
                if mode == "grouped_rebuild":
                    raise error from ExceptionGroup("controlled native group", [cause])
                raise error from cause
        raise error

    runtime = SimpleNamespace(
        _prepare_materialization_catalog_sources_sync=fail_sources,
        _rebuild_in_doc_async=fail_rebuild,
    )
    operations = SimpleNamespace(
        materialization_worker_enabled=lambda: False,
        get_cached_materialized_worker_result=lambda *args, **kwargs: None,
        run_materialization_sources=run,
        run_materialization_cpu=run,
        record_timing=lambda *args: None,
        raise_if_rebuild_request_superseded=lambda *args: None,
        open_readonly_operational_ydoc=doc_session,
        open_rebuild_ydoc_session=doc_session,
        finalize_timing_map=lambda values, **kwargs: dict(values),
    )

    async def call():
        method = "resolve_payload" if mode == "resolve_payload" else "rebuild"
        return await getattr(WebspaceMaterializationService(), method)(
            runtime, operations, "native-failure-test"
        )

    with pytest.raises(type(error)) as captured:
        if cross_thread:
            await asyncio.to_thread(lambda: asyncio.run(call()))
        else:
            await call()
    assert captured.value is error
    assert (owners[0] != threading.get_ident()) is cross_thread
    native_frames = []
    coordinates = []
    pending, visited = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        pending.extend(
            item
            for item in (current.__cause__, current.__context__)
            if item is not None
        )
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        tb = current.__traceback__
        while tb is not None:
            coordinates.append(tb.tb_frame.f_code.co_name)
            if any(
                type(value).__name__ in {"YDoc", "YMap"}
                for value in tb.tb_frame.f_locals.values()
            ):
                native_frames.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
    assert native_frames == []
    if mode == "chained_rebuild":
        assert str(error.__cause__) == "controlled native cause"
    if mode == "grouped_rebuild":
        assert isinstance(error.__cause__, ExceptionGroup)
        assert str(error.__cause__.exceptions[0]) == "controlled native cause"
    assert (
        "fail_sources" if mode == "resolve_payload" else "fail_rebuild"
    ) in coordinates


def test_source_preparation_observation_is_bounded_detached_metadata():
    from adaos.services.scenario.webspace_components.materialization import (
        source_preparation_timings,
    )

    source = {
        "dependency_preflight": {
            "skills": [
                {"name": "builder_skill", "duration_ms": 21.25},
                {"name": "../private", "duration_ms": 99},
            ]
        },
        "source_stage_timings_ms": {
            "selected_ui": 14.5,
            "launcher_metadata": 9,
            "launcher_icons": float("inf"),
            "shared_ui": -1,
            "skill_declarations": True,
            "desktop_scenarios": "private text",
            "private_path": "do-not-export",
        }
    }
    assert source_preparation_timings(source) == {
        "prepare_sources.selected_ui": 14.5,
        "prepare_sources.launcher_metadata": 9,
        "prepare_sources.dependency.builder_skill": 21.25,
    }
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
