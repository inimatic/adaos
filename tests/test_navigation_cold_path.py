"""Cold metadata imports and visible navigation timing contracts."""
import subprocess
import sys
import pytest

from adaos.services.scenario import webspace_runtime as runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("same_request", [False, True])
async def test_rebuild_timing_belongs_to_its_navigation(monkeypatch, same_request):
    from types import SimpleNamespace
    from adaos.services.scenario.webspace_components import rebuild
    from adaos.services.yjs import projection_convergence

    monkeypatch.setattr(rebuild, "time", SimpleNamespace(time=lambda: 1000.0, perf_counter=lambda: 10.0))
    monkeypatch.setattr(projection_convergence, "navigation_is_current", lambda *_: True)
    captured = {}

    class StatusCaptured(Exception):
        pass

    def capture(_webspace, **fields):
        captured.update(fields)
        raise StatusCaptured

    previous = {"request_id": "nav:new" if same_request else "nav:old",
                "requested_at": 900.0, "background": True, "switch_timings_ms": {"total": 77.0}}
    operations = SimpleNamespace(
        describe_rebuild_state=lambda _: previous,
        copy_timing_map=lambda value: dict(value) if value else None,
        pending_materialization_snapshot=lambda *args, **kwargs: {},
        set_rebuild_status=capture,
    )
    with pytest.raises(StatusCaptured):
        await rebuild.WebspaceRebuildService().rebuild(operations, "test", action="scenario_switch_rebuild",
            scenario_id="target", scenario_resolution="explicit", request_id="nav:new", materialization_identity={})
    assert captured["requested_at"] == (900.0 if same_request else 1000.0)
    assert captured["background"] is same_request
    assert captured["switch_timings_ms"] == ({"total": 77.0} if same_request else None)


def test_application_metadata_import_does_not_boot_deployment_transports():
    subprocess.run([sys.executable, "-c", """
import sys
from adaos.services.applications.store import ApplicationStore
assert 'adaos.services.applications.deployment_executor' not in sys.modules
assert 'adaos.services.project_deployment.transport' not in sys.modules
import adaos.services.applications as api
assert 'ApplicationDeploymentExecutor' in dir(api)
from adaos.services.applications import ApplicationDeploymentExecutor
from adaos.services.applications.deployment_executor import ApplicationDeploymentExecutor as direct
assert ApplicationDeploymentExecutor is direct
"""], check=True, timeout=30)


def test_catalog_membership_observation_does_not_start_a_network_owner(monkeypatch):
    from types import SimpleNamespace
    from adaos.services.device_inventory import _hub_link_manager_snapshot

    monkeypatch.delitem(sys.modules, "adaos.services.subnet.link_manager", raising=False)
    assert _hub_link_manager_snapshot() == {}
    assert "adaos.services.subnet.link_manager" not in sys.modules
    monkeypatch.setitem(sys.modules, "adaos.services.subnet.link_manager", SimpleNamespace(
        peek_hub_link_manager_snapshot=lambda: {"members": [{"node_id": "live-member"}]},
    ))
    assert _hub_link_manager_snapshot() == {"members": [{"node_id": "live-member"}]}


def test_peek_keeps_the_owner_uninitialized_and_observes_it_after_start(monkeypatch):
    from types import SimpleNamespace
    from adaos.services.subnet import link_manager

    monkeypatch.setattr(link_manager, "_HUB_MANAGER", None)
    monkeypatch.setattr(link_manager, "get_hub_link_manager", lambda: (_ for _ in ()).throw(
        AssertionError("read model constructed a network owner")))
    assert link_manager.peek_hub_link_manager_snapshot() == {}
    monkeypatch.setattr(link_manager, "_HUB_MANAGER", SimpleNamespace(snapshot=lambda: {"members": ["live"]}))
    assert link_manager.peek_hub_link_manager_snapshot() == {"members": ["live"]}


def test_cached_atomic_navigation_includes_queue_and_live_publication():
    phases = runtime._derive_phase_timings(
        switch_timings_ms={"defer_switch_pointer": 0.0, "total": 390.684},
        rebuild_timings_ms={
            "background_queue_wait": 120.0,
            "resolve_materialization_identity": 238.662,
            "projection_refresh": 11.522,
            "semantic_rebuild": 42.462,
            "live_room_refresh": 592.628,
            "total": 889.034,
        },
        semantic_rebuild_timings_ms={"materialization_cache_hit": 0.0, "total": 0.0},
    )
    assert phases["time_to_accept"] == 390.684
    assert phases["background_queue_wait"] == 120.0
    for phase in ("time_to_first_structure", "time_to_interactive_focus",
                  "time_to_full_hydration", "time_to_pointer_update"):
        assert phases[phase] == 1399.718


def test_resolver_cache_stays_detached_and_materialization_computes_fresh_proof():
    resolved = runtime.WebspaceResolverOutputs(
        webspace_id="cold-proof", scenario_id="test", source_mode="workspace",
        application={"id": "test", "desktop": {"title": "first"}},
    )
    cached = runtime._resolved_outputs_to_cache_payload(resolved)
    resolved.application["desktop"]["title"] = "changed"
    assert cached["application"]["desktop"]["title"] == "first"
    restored = runtime._resolved_outputs_from_cache_payload(cached)
    original = runtime._resolved_outputs_to_materialized_payload(restored)
    current = runtime._resolved_outputs_to_materialized_payload(resolved)
    assert runtime._materialized_payload_branch_fingerprints(original)["ui.application"] != \
        runtime._materialized_payload_branch_fingerprints(current)["ui.application"]
