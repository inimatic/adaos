"""Channel-neutral scenario guidance resolved from an admitted manifest."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from adaos.sdk.skills import invoke as invoke_skill
from adaos.services.scenarios import loader as scenarios_loader


class ScenarioGuidanceError(ValueError):
    """Raised when a scenario guidance descriptor cannot be resolved safely."""


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _localized(value: Any, locale: str) -> str:
    values = _mapping(value)
    selected = "ru" if str(locale).lower().startswith("ru") else "en"
    return str(values.get(selected) or values.get("en") or next(iter(values.values()), ""))


def _resolve_state_value(value: Any, state: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$state."):
        current: Any = state
        for part in value[7:].split("."):
            if not isinstance(current, Mapping) or part not in current:
                raise ScenarioGuidanceError(f"guidance state value is missing: {value}")
            current = current[part]
        return copy.deepcopy(current)
    if isinstance(value, Mapping):
        return {str(key): _resolve_state_value(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_state_value(item, state) for item in value]
    return copy.deepcopy(value)


def _read_readme(scenario_id: str, relative_path: str, *, space: str) -> str:
    root = scenarios_loader.scenario_root_for_space(scenario_id, space).resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ScenarioGuidanceError("guidance readme is outside the scenario root") from exc
    if not candidate.is_file():
        return ""
    return candidate.read_text(encoding="utf-8")


def read_guidance(
    scenario_id: str,
    *,
    locale: str = "en",
    channel: str = "text",
    space: str = "workspace",
) -> dict[str, Any]:
    """Read the static scenario description without executing any provider."""

    manifest = scenarios_loader.read_manifest(scenario_id, space=space)
    guidance = _mapping(manifest.get("guidance"))
    if not guidance:
        raise ScenarioGuidanceError(f"scenario does not declare guidance: {scenario_id}")
    presentation = _mapping(guidance.get("presentation"))
    channels = [str(item) for item in presentation.get("channels") or []]
    selected_channel = str(channel or "text").lower()
    if selected_channel not in channels:
        raise ScenarioGuidanceError(f"scenario guidance does not support channel: {selected_channel}")
    return {
        "schema": "adaos.scenario.guidance_document.v1",
        "scenario_id": str(scenario_id),
        "locale": "ru" if str(locale).lower().startswith("ru") else "en",
        "channel": selected_channel,
        "overview": _localized(guidance.get("overview"), locale),
        "readme": _read_readme(str(scenario_id), str(guidance.get("readme") or "README.md"), space=space),
        "modal_id": str(presentation.get("modal_id") or "") or None,
        "conversational": _mapping(guidance.get("conversational")),
    }


def describe_guidance(
    scenario_id: str,
    *,
    state: Mapping[str, Any],
    locale: str = "en",
    channel: str = "text",
    section: str = "all",
    space: str = "workspace",
) -> dict[str, Any]:
    """Combine static help with workflow-aware guidance from its declared provider."""

    result = read_guidance(scenario_id, locale=locale, channel=channel, space=space)
    manifest = scenarios_loader.read_manifest(scenario_id, space=space)
    workflow = _mapping(_mapping(manifest.get("guidance")).get("workflow"))
    source = _mapping(workflow.get("state_source"))
    if source:
        params = _resolve_state_value(_mapping(source.get("params")), state)
        params.setdefault("locale", result["locale"])
        params.setdefault("channel", result["channel"])
        params.setdefault("section", section)
        target = str(source.get("name") or "")
        try:
            skill_id, operation_id = target.rsplit(".", 1)
        except ValueError as exc:
            raise ScenarioGuidanceError("guidance state source must be a qualified skill operation") from exc
        result["workflow"] = invoke_skill(
            skill_id,
            operation_id,
            params,
        )
    else:
        result["workflow"] = None
    return result


__all__ = [
    "ScenarioGuidanceError",
    "describe_guidance",
    "read_guidance",
]
