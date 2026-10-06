from __future__ import annotations

from adaos.services.scenario import webspace_runtime
from adaos.services.scenario.webspace_components import WebspaceRuntimeContainer


def test_webspace_runtime_container_owns_independent_mutable_collaborators() -> None:
    first = WebspaceRuntimeContainer.create_default()
    second = WebspaceRuntimeContainer.create_default()

    assert first.tasks is not second.tasks
    assert first.cache is not second.cache
    assert first.scenario_switching is not second.scenario_switching


def test_webspace_facade_exposes_one_container_without_legacy_owner_aliases() -> None:
    assert isinstance(webspace_runtime._RUNTIME, WebspaceRuntimeContainer)
    assert not hasattr(webspace_runtime, "_TASK_STATE")
    assert not hasattr(webspace_runtime, "_CACHE_STATE")


def test_materialization_cache_size_accounts_for_binary_without_python_tree_walk(monkeypatch):
    runtime = WebspaceRuntimeContainer.create_default()
    monkeypatch.setattr(webspace_runtime, "_RUNTIME", runtime)
    monkeypatch.setattr(webspace_runtime, "_approximate_cache_size_bytes", lambda *_: (_ for _ in ()).throw(AssertionError("recursive traversal")))
    payload = {"webui": {"widgets": [{"id": str(i), "label": "widget"} for i in range(100)]}}
    webspace_runtime._remember_materialized_worker_result_in_memory("fixture", {
        "materialized_payload": payload, "snapshot_update": b"x" * 4096, "state_vector": b"v",
    })
    cached = runtime.cache.get_materialized_webspace("fixture")
    assert cached["_cache_size_bytes"] > 4096
    assert cached["materialized_payload"] == payload
