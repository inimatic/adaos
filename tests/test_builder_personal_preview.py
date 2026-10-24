"""Consumer Preview admission must recognize its pinned owning Project."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest


@pytest.mark.parametrize('kind,target_id,target_scenario,owner,follow,accepted', [
    ('scenario', 'primary', None, 'project:owner', True, True),
    ('project', 'owner', 'primary', 'project:owner', True, True),
    ('project', 'other', 'primary', 'project:owner', True, False),
    ('project', 'owner', 'other', 'project:owner', True, False),
    ('project', 'owner', 'primary', '', True, False),
    ('project', 'owner', 'primary', 'project:owner', False, False),
])
def test_follow_active_preview_keeps_the_exact_project_and_primary(
    kind, target_id, target_scenario, owner, follow, accepted,
):
    source_root = os.environ.get('ADAOS_TEST_BUILDER_SOURCE')
    if not source_root:
        pytest.skip('Private Builder source qualification requires an explicit source path')
    source = Path(source_root) / 'handlers/main.py'
    module = ast.parse(source.read_text(encoding='utf-8-sig'))
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_refresh_follow_active_preview')
    calls = []
    namespace = {'Mapping': Mapping, 'Any': Any, 'builder_preview': SimpleNamespace(
        refresh_follow_active_target=lambda *args, **kwargs: calls.append((args, kwargs)) or {'ok': True})}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    result = namespace['_refresh_follow_active_preview']('owned-host',
        session={'scenario_id': 'primary', 'project_ref': owner},
        binding={'preview_target': {'object_type': kind, 'object_id': target_id,
                                   'scenario_id': target_scenario, 'follow_active': follow}}, revision='002')
    assert bool(calls) is accepted
    if accepted:
        assert calls[0][0] == ('scenario', 'primary')
        assert calls[0][1]['source_webspace_id'] == 'owned-host'
        assert calls[0][1]['revision'] == '002'
    else:
        assert 'skipped' in result


def test_sdk_follow_active_revision_schedules_owner_reconciliation(monkeypatch):
    from adaos.sdk.builder import preview
    from test_builder_preview_sdk import _Workbench
    from adaos.domain.project_events import BUILDER_PREVIEW_DESIRED
    service = _Workbench()
    service.target.update(object_type='project', object_id='owner', scenario_id='recipes')
    monkeypatch.setattr(preview, '_service', lambda: service)
    events = []
    monkeypatch.setattr('adaos.sdk.data.events.publish', lambda *args, **kwargs: events.append((args, kwargs)))
    result = preview.refresh_follow_active_target('scenario', 'recipes', revision='005')
    assert result['materialization'] == 'deferred'
    assert result['target']['object_type'] == 'project'
    assert events[0][0][0] == BUILDER_PREVIEW_DESIRED
    assert events[0][0][1] == {'source_webspace_id': 'desktop', 'object_type': 'scenario',
        'object_id': 'recipes', 'scenario_id': 'recipes', 'revision': '005',
        'reason': 'builder_follow_active_revision', 'reconciled': False, 'wait_for_rebuild': False}


@pytest.mark.asyncio
@pytest.mark.parametrize('revision,follow,scenario,stage,accepted', [
    ('005', True, 'primary', 'prototype', True),
    ('004', True, 'primary', 'prototype', False),
    ('005', False, 'primary', 'prototype', False),
    ('005', True, 'foreign', 'prototype', False),
    ('005', True, 'primary', 'automation', False),
])
async def test_owner_reconciles_only_the_current_follow_active_revision(
    monkeypatch, revision, follow, scenario, stage, accepted,
):
    from adaos.services.builder import workbench
    calls = []
    class Workbench:
        def get_workspace_binding(self, source):
            assert source == 'owned-host'
            return {'preview_target': {'object_type': 'project', 'object_id': 'owner',
                'scenario_id': 'primary', 'revision': '005', 'follow_active': follow, 'stage': stage}}
        async def ensure_dev_webspace(self, source, **kwargs):
            calls.append((source, kwargs))
    monkeypatch.setattr(workbench, 'BuilderWorkbenchService', Workbench)
    await workbench._on_builder_preview_selected({'source_webspace_id': 'owned-host',
        'object_type': 'scenario', 'scenario_id': scenario, 'revision': revision,
        'reason': 'builder_follow_active_revision'})
    assert bool(calls) is accepted
    if accepted:
        assert calls[0][0] == 'owned-host'
        assert calls[0][1]['force_runtime_reload'] is True
        assert calls[0][1]['wait_for_rebuild'] is False
