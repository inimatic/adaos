"""Public SDK facade for bounded Builder LLM UI Studio experiments."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.ui_studio import BuilderUIStudioService

    return BuilderUIStudioService.from_context()


def _project_ref(object_type: str, object_id: str) -> str:
    kind = str(object_type or "").strip().lower().rstrip("s")
    project_id = str(object_id or "").strip()
    if kind not in {"project", "scenario"} or not project_id:
        raise ValueError("UI Studio requires project/scenario and object_id")
    return f"{kind}:{project_id}"


def profiles() -> dict[str, Any]:
    """List compiler-owned UI profiles and their bounded option spaces."""

    return _service().profiles()


def inventory(object_type: str, object_id: str) -> dict[str, Any]:
    """Read the compact model-facing inventory, never the full WebUI prompt."""

    return _service().inventory(_project_ref(object_type, object_id))


def state(object_type: str, object_id: str) -> dict[str, Any]:
    """Read the latest isolated experiment for one selected Builder project."""

    return _service().latest(_project_ref(object_type, object_id))


def inspect(experiment_id: str) -> dict[str, Any]:
    return _service().inspect(experiment_id)


def create(intent: Mapping[str, Any]) -> dict[str, Any]:
    """Compile and evaluate deterministic variants from a validated intent."""

    return _service().create_experiment(intent)


def benchmark(
    object_type: str,
    object_id: str,
    *,
    benchmark_id: str = "small-cities.v1",
) -> dict[str, Any]:
    """Run a named repeatable vertical benchmark through the same compiler."""

    if benchmark_id != "small-cities.v1":
        raise ValueError(f"unknown UI Studio benchmark: {benchmark_id}")
    from adaos.services.builder.ui_studio import small_cities_benchmark_intent

    intent = small_cities_benchmark_intent(_project_ref(object_type, object_id))
    return _service().create_experiment(intent)


def propose(
    object_type: str,
    object_id: str,
    *,
    prompt: str,
    model: str,
    reasoning_effort: str = "medium",
    max_tokens: int = 8192,
    timeout_s: float = 300,
) -> dict[str, Any]:
    """Ask an ordinary AdaOS LLM for compact intent; Codex is not involved."""

    return _service().propose(
        project_ref=_project_ref(object_type, object_id),
        prompt=prompt,
        model=model,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
    )


def select(experiment_id: str, option_id: str) -> dict[str, Any]:
    """Select a passed alternative without changing the project revision."""

    return _service().select(experiment_id, option_id)


def stage(experiment_id: str, option_id: str | None = None) -> dict[str, Any]:
    """Stage an EXP revision; the accepted current pointer remains unchanged."""

    return _service().stage(experiment_id, option_id)


def preview(
    experiment_id: str,
    option_id: str | None = None,
    *,
    source_webspace_id: str = "desktop",
    via_owner: bool = True,
) -> dict[str, Any]:
    """Stage and materialize one exact candidate in the paired Preview Webspace."""

    staged = stage(experiment_id, option_id)
    from . import preview as preview_sdk

    materialized = preview_sdk.select_target(
        "scenario",
        staged["scenario_id"],
        stage="prototype",
        revision=staged["revision"],
        source_webspace_id=source_webspace_id,
        follow_active=False,
        via_owner=via_owner,
    )
    return {
        "ok": bool(materialized.get("ok", True)),
        "experiment": staged["experiment"],
        "staged_revision": staged["revision"],
        "current_pointer_unchanged": staged["current_pointer_unchanged"],
        "preview": materialized,
    }


__all__ = [
    "create",
    "benchmark",
    "inspect",
    "inventory",
    "preview",
    "profiles",
    "propose",
    "select",
    "stage",
    "state",
]
