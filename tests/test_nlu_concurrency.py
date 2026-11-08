from __future__ import annotations

import asyncio
import threading

import pytest
import yaml

from adaos.services.agent_context import get_ctx
from adaos.services.nlu import dispatcher, entity_resolver_runtime


def test_reply_from_another_turn_is_not_proof_that_this_turn_already_replied():
    raw = {'request_id': 'max:slow-turn', '_meta': {'route_id': 'max'}}
    other = {'text': 'Готово', '_meta': {'route_id': 'max', 'webspace_id': 'desktop',
                                      'request_id': 'max:fast-turn'}}
    assert not dispatcher._chat_append_matches_action(other, text='Готово', raw=raw, webspace_id='desktop')
    own = {**other, '_meta': {**other['_meta'], 'request_id': 'max:slow-turn'}}
    assert dispatcher._chat_append_matches_action(own, text='Готово', raw=raw, webspace_id='desktop')
    # The interpreter may have an internal request ID while transport metadata
    # retains the canonical source turn's ID.
    internal = {'request_id': 'nlu:internal', '_meta': {'route_id': 'max', 'request_id': 'max:slow-turn'}}
    assert dispatcher._chat_append_matches_action(own, text='Готово', raw=internal, webspace_id='desktop')


@pytest.mark.anyio
@pytest.mark.parametrize('blocking_stage', ['catalog', 'tool'])
async def test_slow_max_turn_does_not_block_next_turn_or_duplicate_action(monkeypatch, blocking_stage):
    ctx = get_ctx()
    loop = asyncio.get_running_loop()
    started = loop.create_future()
    release = threading.Event()
    calls = []
    tasks = []
    watchdog = threading.Timer(5, release.set)
    for name in ('slow_provider', 'fast_provider'):
        path = ctx.paths.skills_dir() / name / 'skill.yaml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({'nlu': {'intents': {name: {'actions': [
            {'type': 'skillTool', 'skill': name, 'tool': 'query'}
        ]}}}}), encoding='utf8')

    async def scenario(_ctx, _webspace):
        return 'applications'

    def block():
        loop.call_soon_threadsafe(started.set_result, None)
        assert release.wait(10), 'test did not release the slow provider'

    scan = dispatcher._skill_nlu_actions_for_intent
    blocked_catalog = False

    def catalog(context, intent):
        nonlocal blocked_catalog
        if blocking_stage == 'catalog' and intent == 'slow_provider' and not blocked_catalog:
            blocked_catalog = True
            block()
        return scan(context, intent)

    def tool(context, skill, method, payload):
        assert get_ctx() is ctx is context
        if blocking_stage == 'tool' and skill == 'slow_provider':
            block()
        calls.append((skill, dict(payload['_meta'])))
        return {'ok': True}

    monkeypatch.setattr(dispatcher, '_resolve_scenario_id', scenario)
    monkeypatch.setattr(dispatcher, '_load_scenario_nlu', lambda _: {})
    monkeypatch.setattr(dispatcher, '_skill_nlu_actions_for_intent', catalog)
    monkeypatch.setattr(dispatcher, '_run_skill_tool', tool)
    monkeypatch.setattr(dispatcher, '_DISPATCHED_REQUESTS', {})

    def request(skill):
        return {'intent': skill, 'confidence': 1, 'webspace_id': 'desktop',
                'request_id': 'max-concurrency:' + skill,
                '_meta': {'route_id': 'max', 'io_type': 'max', 'binding_id': 'binding:owned',
                          'source_message_id': 'message:' + skill}}

    watchdog.start()
    try:
        slow = asyncio.create_task(dispatcher._on_nlp_intent_detected(request('slow_provider')))
        tasks.append(slow)
        await asyncio.wait_for(asyncio.shield(started), timeout=10)
        assert not release.is_set(), 'NLU blocked the event loop until the provider finished'
        fast = asyncio.create_task(dispatcher._on_nlp_intent_detected(request('fast_provider')))
        tasks.append(fast)
        await asyncio.wait_for(asyncio.shield(fast), timeout=3)
        assert not release.is_set(), 'the next MAX turn waited for the slow turn'
        assert not slow.done()
        if blocking_stage == 'tool':
            duplicate = asyncio.create_task(dispatcher._on_nlp_intent_detected(request('slow_provider')))
            tasks.append(duplicate)
            await asyncio.wait_for(asyncio.shield(duplicate), timeout=3)
        release.set()
        await asyncio.gather(*tasks)
        assert [skill for skill, _ in calls] == ['fast_provider', 'slow_provider']
        for skill, meta in calls:
            assert meta['source_message_id'] == 'message:' + skill
            assert meta['route_id'] == 'max'
            assert meta['binding_id'] == 'binding:owned'
    finally:
        release.set()
        watchdog.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_entity_diagnostics_do_not_block_following_dialog_events(monkeypatch):
    loop = asyncio.get_running_loop()
    started = loop.create_future()
    release = threading.Event()
    watchdog = threading.Timer(5, release.set)
    emitted = []

    def build(payload):
        assert payload['request_id'] == 'max:entity-probe'
        loop.call_soon_threadsafe(started.set_result, None)
        assert release.wait(10)
        return {'stage': 'named_entity', 'request_id': payload['request_id']}

    monkeypatch.setattr(entity_resolver_runtime, 'build_entity_trace_stage', build)
    get_ctx().bus.subscribe('nlu.trace.stage', lambda ev: emitted.append(ev.payload))
    watchdog.start()
    task = asyncio.create_task(entity_resolver_runtime.on_detect_request({'request_id': 'max:entity-probe'}))
    try:
        await asyncio.wait_for(asyncio.shield(started), timeout=10)
        assert not release.is_set(), 'entity diagnostics blocked the runtime loop'
        # An unrelated dialog callback can run while the diagnostic lookup waits.
        tick = loop.create_future()
        loop.call_soon(tick.set_result, True)
        assert await tick
        assert not task.done()
        release.set()
        await task
        assert emitted == [{'stage': 'named_entity', 'request_id': 'max:entity-probe'}]
    finally:
        release.set()
        watchdog.cancel()
        await task
