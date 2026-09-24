from __future__ import annotations

from types import SimpleNamespace

from adaos.services.scenario import webspace_runtime


def _clear_local_display_cache() -> None:
    webspace_runtime._RUNTIME.cache.put_local_node_display(0.0, {})


def test_local_node_identity_uses_bootstrap_config_snapshot(monkeypatch) -> None:
    config = SimpleNamespace(node_id="node-runtime", role="hub")
    monkeypatch.setattr(
        webspace_runtime,
        "get_ctx",
        lambda: SimpleNamespace(config=config),
    )
    monkeypatch.setattr(
        webspace_runtime,
        "node_display_from_config",
        lambda observed: {
            "node_label": "Runtime node" if observed is config else "wrong config",
            "node_compact_label": "N0",
            "node_index": 0,
            "node_color": "",
        },
    )
    _clear_local_display_cache()

    assert webspace_runtime._local_node_id() == "node-runtime"
    assert webspace_runtime._local_node_label() == "Runtime node"


def test_local_node_identity_falls_back_without_storage_access(monkeypatch) -> None:
    class _RuntimeContext(webspace_runtime.AgentContext):
        pass

    runtime_context = object.__new__(_RuntimeContext)
    object.__setattr__(runtime_context, "config", None)
    monkeypatch.setattr(webspace_runtime, "get_ctx", lambda: runtime_context)
    monkeypatch.setattr(
        webspace_runtime,
        "load_config",
        lambda: (_ for _ in ()).throw(AssertionError("must not access storage")),
    )
    monkeypatch.setattr(
        webspace_runtime,
        "node_display_from_config",
        lambda _config: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    _clear_local_display_cache()

    assert webspace_runtime._local_node_id() == "hub"
    assert webspace_runtime._local_node_display()["node_label"] == "hub"
