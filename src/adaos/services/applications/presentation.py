"""Catalog presentation fallbacks; never infer product artwork from identity."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def primary_scenario_entrypoint(entrypoints: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Choose one Application launcher; supporting scenarios stay internal."""
    scenarios = [entry for entry in entrypoints
                 if str(entry.get('presentation_ref') or '').startswith('scenario:')]
    return next((entry for entry in scenarios
                 if entry.get('default') is True
                 or entry.get('entrypoint_id') in {'main', 'default', 'primary'}),
                scenarios[0] if scenarios else None)


def scenario_launcher_target(item: Mapping[str, Any]) -> str:
    """Read the scenario navigation used by desktop catalog contributions."""
    scenario = str(item.get('scenario_id') or item.get('scenarioId') or '').strip()
    if scenario:
        return scenario
    identity = str(item.get('id') or '')
    if identity.startswith('scenario:'):
        return identity.removeprefix('scenario:')
    if ':scenario:' in identity:
        return identity.split(':scenario:', 1)[1]
    action = item.get('action')
    if isinstance(action, Mapping) and (action.get('target') or action.get('callHost')) == 'desktop.scenario.set':
        params = action.get('params') or action.get('arguments') or {}
        if isinstance(params, Mapping):
            return str(params.get('scenario_id') or '').strip()
    return ''


def icon_fallback(title: str) -> dict[str, str]:
    words = str(title or "").split()
    text = "".join(word[0] for word in words[:2]).upper()
    return {"iconText": text or "?"}


def catalog_icon(title: str, *declared_icons: str | None) -> dict[str, str]:
    icon = next((value.strip() for value in declared_icons if isinstance(value, str) and value.strip()), '')
    return {'icon': icon or 'apps-outline', **({} if icon else icon_fallback(title))}
