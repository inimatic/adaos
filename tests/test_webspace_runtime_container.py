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
