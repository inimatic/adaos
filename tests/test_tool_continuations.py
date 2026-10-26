import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from starlette.requests import Request

from adaos.apps.api.tool_bridge import ToolCall
from adaos.domain.personalization_access import SubjectRef
from adaos.services.policy.caller import verified_caller
from adaos.services.policy.invocation import verified_invocation
from adaos.services import tool_continuations as queue


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "handler.py").write_text("version = 1\n")
    manifest = tmp_path / "resolved.json"
    manifest.write_text(json.dumps({"source": str(source), "tools": {"run": {"durable_replay": True}}}))
    manager = SimpleNamespace(runtime_status=lambda skill: {"resolved_manifest": str(manifest)},
                              dev_runtime_status=lambda skill: {"resolved_manifest": str(manifest)})
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path / "state"))
    body = ToolCall(tool="sample:run", request_id="intent-1", idempotency_key="intent-1",
                    intent="mutation", arguments={"photo_id": "photo-1", "mask": {"version": 5}},
                    context={"webspace_id": "desktop-dev"}, dev=True)
    request = Request({"type": "http", "method": "POST", "path": "/api/tools/call", "headers": []})
    monkeypatch.setattr("adaos.sdk.access.require", lambda capability: {"decision": "allow"})
    return SimpleNamespace(ctx=ctx, body=body, manager=manager, request=request,
                           source=source, manifest=manifest, actor=SubjectRef("user", "owner"))


def register(f, *, key="run-1", mutating=True, ttl=3600):
    with verified_caller(f.actor), verified_invocation(request_id=f.body.request_id, idempotency_key="intent-1"):
        with queue.admitted_tool(f.ctx, f.manager, f.body, f.request, mutating=mutating):
            return queue.defer_current(key=key, retry_after_s=1, ttl_s=ttl)


def due(f):
    with queue._connect(queue._database(f.ctx)) as db:
        db.execute("UPDATE continuations SET due=0")


def record(f):
    with queue._connect(queue._database(f.ctx)) as db:
        return dict(db.execute("SELECT * FROM continuations").fetchone())


def test_sdk_replay_observation_uses_only_core_context_and_resets():
    from adaos.sdk import continuations
    assert continuations.is_replay() is False
    marker=queue._REPLAY.set({"pin":"admitted"})
    try:
        assert continuations.is_replay() is True
    finally:
        queue._REPLAY.reset(marker)
    assert continuations.is_replay() is False


def test_requires_verified_mutating_ingress_and_opt_in(fixture):
    f = fixture
    with pytest.raises(PermissionError, match="admitted_mutation"):
        queue.defer_current(key="run", retry_after_s=1, ttl_s=60)
    with pytest.raises(PermissionError, match="admitted_mutation"):
        register(f, mutating=False)
    f.body = f.body.model_copy(update={"request_id": None})
    with pytest.raises(PermissionError, match="stable_invocation"):
        register(f)
    f.body = f.body.model_copy(update={"request_id": "intent-1"})
    data = json.loads(f.manifest.read_text())
    data["tools"]["run"]["durable_replay"] = False
    f.manifest.write_text(json.dumps(data))
    with pytest.raises(PermissionError, match="declare_durable_replay"):
        register(f)


def test_atomic_arm_idempotency_and_binding_cannot_change(fixture):
    f = fixture
    first = register(f)
    assert first["status"] == "armed"
    assert record(f)["status"] == "waiting"
    second = register(f, ttl=7200)
    assert first["id"] == second["id"]
    assert first["expires_at"] == second["expires_at"]
    f.body = f.body.model_copy(update={"arguments": {"photo_id": "other", "mask": {"version": 6}}})
    with pytest.raises(ValueError, match="binding_conflict"):
        register(f)


def test_read_tool_can_observe_but_cannot_complete_even_for_owner(fixture):
    f = fixture
    register(f)
    with verified_caller(f.actor), verified_invocation(request_id="read-1", idempotency_key="read-1"):
        with queue.admitted_tool(f.ctx, f.manager, f.body, f.request, mutating=False):
            assert queue.current_record("run-1")["status"] == "waiting"
            with pytest.raises(PermissionError, match="admitted_mutation"):
                queue.current_record("run-1", outcome="completed")


def test_runtime_compilation_preserves_and_revokes_replay_opt_in(tmp_path):
    from adaos.services.skill.manager import SkillManager
    manager = object.__new__(SkillManager)
    source = tmp_path / "source"
    source.mkdir()
    declaration = source / "skill.yaml"
    declaration.write_text("tools:\n  - name: run\n    entry: handlers.run:run\n    durable_replay: true\n")
    resolved = tmp_path / "resolved.json"
    resolved.write_text(json.dumps({"tools": {}}))
    manager._runtime_sync_manifest_tools("sample", resolved, source)
    assert json.loads(resolved.read_text())["tools"]["run"]["durable_replay"] is True
    declaration.write_text(declaration.read_text().replace("true", "false"))
    manager._runtime_sync_manifest_tools("sample", resolved, source)
    assert json.loads(resolved.read_text())["tools"]["run"]["durable_replay"] is False


def test_does_not_store_credentials_or_replay_one_time_approval(fixture):
    f = fixture
    f.body = f.body.model_copy(update={"arguments": {"action_approval": {"approved": True},
        "_meta": {"action_approval": {"secret": "one-time"}, "approval_ref": {"id": "one-time"},
                  "action_source": "operator_ui", "pending_action_id": "one-time"}, "photo_id": "photo-1"},
        "context": {"_verified_application_access": {"_ingress_authorized_permission_id": "write"},
                    "webspace_id": "desktop-dev"}})
    f.request = Request({"type": "http", "headers": [(b"authorization", b"Bearer private-token"),
                                                      (b"x-adaos-token", b"private-token")]})
    register(f)
    binding = record(f)["binding"]
    assert "one-time" not in binding
    assert "private-token" not in binding
    assert "_ingress_authorized" not in binding
    assert "operator_ui" not in binding
    assert json.loads(binding)["body"]["context"]["webspace_id"] == "desktop-dev"


def test_single_lease_and_crash_recovery(fixture):
    f = fixture
    register(f)
    due(f)
    path = queue._database(f.ctx)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: queue._claim(path, time.time()), range(2)))
    assert sum(item is not None for item in claims) == 1
    old = next(item for item in claims if item)
    recovered = queue._claim(path, time.time() + 121)
    assert recovered["id"] == old["id"]
    assert recovered["lease"] != old["lease"]


def test_expired_work_never_dispatches(fixture, monkeypatch):
    f = fixture
    register(f, ttl=10)
    path = queue._database(f.ctx)
    assert queue._claim(path, time.time() + 11) is None
    assert record(f)["status"] == "expired"


@pytest.mark.asyncio
async def test_restart_queued_edit_qa_export_with_same_snapshot_and_no_duplicate_submit(fixture, monkeypatch):
    f = fixture
    register(f)
    effects = []
    state = {"stage": "edit", "poll": 0, "jobs": {}}

    async def dispatch(ctx, binding):
        assert queue.is_replay()
        assert binding["identity"]["idempotency_key"] == "intent-1"
        assert binding["body"]["arguments"]["mask"]["version"] == 5
        with verified_caller(f.actor), verified_invocation(**binding["identity"]):
            with queue.admitted_tool(ctx, f.manager, ToolCall(**binding["body"]), f.request, mutating=True):
                queue.defer_current(key="run-1", retry_after_s=1, ttl_s=3600)
                phase = state["stage"]
                if phase not in state["jobs"]:
                    state["jobs"][phase] = "job-" + phase
                    effects.append(phase)
                    return
                state["poll"] += 1
                if phase == "edit":
                    state["stage"] = "qa"
                else:
                    effects.append("export")
                    queue.current_record("run-1", outcome="completed")

    monkeypatch.setattr(queue, "_dispatch", dispatch)
    # No browser, no in-memory queue object. Each tick reopens persisted SQLite.
    for _ in range(4):
        due(f)
        assert await queue.run_once(f.ctx)
    assert effects == ["edit", "qa", "export"]
    assert state["poll"] == 2
    assert record(f)["status"] == "completed"
    assert not await queue.run_once(f.ctx)
    assert not queue.is_replay()


@pytest.mark.asyncio
async def test_changed_source_blocks_before_effect_and_does_not_retry_exception(fixture, monkeypatch):
    f = fixture
    register(f)
    (f.source / "handler.py").write_text("version = 2\n")
    effects = []

    async def dispatch(ctx, binding):
        with queue.admitted_tool(ctx, f.manager, ToolCall(**binding["body"]), f.request, mutating=True):
            effects.append("paid")

    monkeypatch.setattr(queue, "_dispatch", dispatch)
    due(f)
    assert await queue.run_once(f.ctx)
    assert record(f)["status"] == "blocked"
    assert "runtime_changed" in record(f)["error"]
    assert effects == []
    assert not await queue.run_once(f.ctx)


@pytest.mark.asyncio
async def test_revoked_access_blocks_without_automatic_retry(fixture, monkeypatch):
    f = fixture
    register(f)

    async def revoked(ctx, binding):
        raise PermissionError("session_revoked")

    monkeypatch.setattr(queue, "_dispatch", revoked)
    due(f)
    await queue.run_once(f.ctx)
    assert record(f)["status"] == "blocked"
    assert "session_revoked" in record(f)["error"]
    assert not await queue.run_once(f.ctx)


@pytest.mark.asyncio
async def test_draining_before_dispatch_waits_and_resumes_same_intent(fixture, monkeypatch):
    from fastapi import HTTPException
    f=fixture;register(f);due(f);bindings=[]
    async def draining(ctx,binding):
        bindings.append(binding)
        raise HTTPException(503,{'error':'node_draining','retryable':True})
    monkeypatch.setattr(queue,'_dispatch',draining)
    await queue.run_once(f.ctx)
    assert record(f)['status']=='waiting'
    due(f)
    async def admitted(ctx,binding):bindings.append(binding)
    monkeypatch.setattr(queue,'_dispatch',admitted)
    await queue.run_once(f.ctx)
    assert bindings[0]==bindings[1]
    assert record(f)['attempts']==2


@pytest.mark.parametrize('error,expired,restored',[
    ("HTTPException: 503: {'error': 'node_draining', 'retryable': True}",False,1),
    ("HTTPException: 503: {'error': 'node_draining', 'retryable': True}",True,0),
    ("HTTPException: 503: {'error': 'node_draining', 'retryable': False}",False,0),
    ("HTTPException: 503: {'error': 'outcome_unknown', 'retryable': True}",False,0),
    ('PermissionError: session_revoked',False,0),
    ("HTTPException: 503: malformed",False,0),
])
def test_restart_reconciles_only_unexpired_pre_dispatch_drain_refusals(fixture,error,expired,restored):
    f=fixture;register(f);before=record(f)
    with queue._connect(queue._database(f.ctx)) as db:
        db.execute("UPDATE continuations SET status='blocked',error=?,expires=?",(error,0 if expired else before['expires']))
    assert queue.reconcile_draining_rejections(f.ctx)==restored
    after=record(f)
    assert after['binding']==before['binding'] and after['expires']==(0 if expired else before['expires'])
    assert after['status']==('waiting' if restored else 'blocked')


@pytest.mark.parametrize('change', ['none','code','permission','version','corrupt_old'])
def test_identical_slot_repack_keeps_pin_but_substantive_changes_do_not(tmp_path,change):
    pins=[]
    for slot in ('A','B'):
        root=tmp_path/slot;source=root/'src';source.mkdir(parents=True)
        (source/'handler.py').write_text('version = 1\n')
        manifest=root/'resolved.json'
        manifest.write_text(json.dumps({'source':str(source),'slot_root':str(root),'slot':slot,
            'version':'1','capabilities':['workspace.read'],'tools':{'run':{'handler':'handler.py'}},
            'runtime':{'src':str(source)}}))
        pins.append(queue._manifest_pin(manifest))
    assert queue._same_runtime(*pins)
    target=tmp_path/'B'
    if change=='code':(target/'src/handler.py').write_text('version = 2\n')
    if change in ('permission','version'):
        p=target/'resolved.json';d=json.loads(p.read_text());d['capabilities' if change=='permission' else 'version']='changed';p.write_text(json.dumps(d))
    if change=='corrupt_old':(tmp_path/'A/src/handler.py').write_text('version = 3\n')
    current=queue._manifest_pin(target/'resolved.json')
    assert queue._same_runtime(pins[0],current) is (change=='none')


def test_replay_registration_keeps_frozen_binding_after_identical_slot_copy(fixture):
    import shutil
    f=fixture;d=json.loads(f.manifest.read_text());d.update(slot_root=str(f.manifest.parent),slot='A');f.manifest.write_text(json.dumps(d))
    first=register(f);binding=json.loads(record(f)['binding'])
    root=f.manifest.parent/'B';root.mkdir();source=root/'source';shutil.copytree(f.source,source)
    d.update(source=str(source),slot_root=str(root),slot='B');manifest=root/'resolved.json';manifest.write_text(json.dumps(d))
    f.manager=SimpleNamespace(runtime_status=lambda _: {'resolved_manifest':str(manifest)},dev_runtime_status=lambda _: {'resolved_manifest':str(manifest)})
    marker=queue._REPLAY.set(binding)
    try: second=register(f)
    finally:queue._REPLAY.reset(marker)
    assert second['id']==first['id']
    assert json.loads(record(f)['binding'])==binding


@pytest.mark.asyncio
async def test_background_reenters_real_ingress_with_caller_and_no_auth_secret(fixture, monkeypatch):
    from adaos.apps.api import tool_bridge
    f = fixture
    register(f)
    binding = json.loads(record(f)["binding"])
    calls = []

    async def gate(body, request, response, ctx):
        calls.append((body, request))
        assert request.state.adaos_verified_caller == f.actor
        assert body.context["webspace_id"] == "desktop-dev"
        assert request.headers.get("X-AdaOS-Token") is None

    monkeypatch.setattr(tool_bridge, "call_tool", gate)
    await queue._dispatch(f.ctx, binding)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_public_route_and_verified_development_boundary_are_retained(fixture, monkeypatch):
    from adaos.apps.api import tool_bridge
    from adaos.services.integrations.ingress import ingress_environment_for_http_route, PUBLIC_CONNECTED_ENVIRONMENT_REF
    f = fixture
    f.request = Request({"type": "http", "headers": [(b"x-adaos-route", b"root-routed")]})
    f.request.state.adaos_root_routed = True
    f.request.state.adaos_development_access = False
    register(f)
    binding = json.loads(record(f)["binding"])
    async def admitted(body, request, response, ctx):
        assert request.state.adaos_root_routed is True
        assert request.state.adaos_development_access is False
        assert ingress_environment_for_http_route(request.headers) == PUBLIC_CONNECTED_ENVIRONMENT_REF
    monkeypatch.setattr(tool_bridge, "call_tool", admitted)
    await queue._dispatch(f.ctx, binding)


@pytest.mark.asyncio
async def test_wrapped_tool_failure_does_not_become_endless_replay(fixture, monkeypatch):
    from adaos.apps.api import tool_bridge
    f = fixture
    register(f)
    async def failed(*args):
        return {"ok": True, "result": {"ok": False, "error": "snapshot_missing"}}
    monkeypatch.setattr(tool_bridge, "call_tool", failed)
    due(f)
    await queue.run_once(f.ctx)
    assert record(f)["status"] == "blocked"
    assert "snapshot_missing" in record(f)["error"]
    assert not await queue.run_once(f.ctx)


def approved_action(reference, *, status='responded', choice='approve', tool='sample:run', webspace='desktop-dev'):
    return {'id': reference, 'kind': 'runtime.action_approval', 'status': status,
            'response': {'response_action_id': choice},
            'domain_ref': {'tool': tool, 'webspace_id': webspace}}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['approved', 'pending', 'refused', 'absent', 'expired', 'wrong_tool', 'wrong_webspace', 'ambiguous_legacy_prefix'])
async def test_only_exact_recorded_approval_wakes_unexpired_work(fixture, monkeypatch, case):
    from adaos.services import pending_actions
    f = fixture
    register(f)
    before = record(f)
    reference = 'pa.runtime_action.' + 'a' * 24
    prefix = reference[:len('pa.runtime_action.') + 12] if case == 'ambiguous_legacy_prefix' else reference
    error = "HTTPException: 403: {'error': 'action_approval_required', 'pending_action_id': '" + prefix
    with queue._connect(queue._database(f.ctx)) as db:
        db.execute("UPDATE continuations SET status='blocked',error=?,expires=?", (error, 0 if case == 'expired' else before['expires']))
    action = approved_action(reference, status='pending' if case == 'pending' else 'responded',
                             choice='refuse' if case == 'refused' else 'approve',
                             tool='other:run' if case == 'wrong_tool' else 'sample:run',
                             webspace='desktop' if case == 'wrong_webspace' else 'desktop-dev')
    actions = {} if case == 'absent' else {reference: action}
    if case == 'ambiguous_legacy_prefix':
        other = reference[:-1] + 'b'
        actions[other] = approved_action(other)
    async def snapshot(**kwargs):
        assert kwargs['webspace_id'] == 'desktop-dev'
        return {'by_id': actions}
    monkeypatch.setattr(pending_actions, 'list_pending_actions_async', snapshot)
    assert await queue.reconcile_approved_action_rejections(f.ctx) == (1 if case == 'approved' else 0)
    after = record(f)
    assert after['binding'] == before['binding']
    assert after['expires'] == (0 if case == 'expired' else before['expires'])
    assert after['status'] == ('waiting' if case == 'approved' else 'blocked')
    assert await queue.reconcile_approved_action_rejections(f.ctx) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('subsequent', ['success', 'access_revoked', 'runtime_changed'])
async def test_approval_wakeup_reenters_all_gates_without_changing_intent(fixture, monkeypatch, subsequent):
    from fastapi import HTTPException
    from adaos.services import pending_actions
    f = fixture
    register(f)
    due(f)
    reference = 'pa.runtime_action.' + 'b' * 24
    original = record(f)
    async def waiting(ctx, binding):
        raise HTTPException(403, {'error': 'action_approval_required', 'large_explanation': 'x' * 2000,
                                  'pending_action_id': reference})
    monkeypatch.setattr(queue, '_dispatch', waiting)
    await queue.run_once(f.ctx)
    assert record(f)['status'] == 'blocked'
    assert reference in record(f)['error']
    async def snapshot(**kwargs): return {'by_id': {reference: approved_action(reference)}}
    monkeypatch.setattr(pending_actions, 'list_pending_actions_async', snapshot)
    assert await queue.reconcile_approved_action_rejections(f.ctx) == 1
    effects = []
    if subsequent == 'runtime_changed':
        (f.source / 'handler.py').write_text('version = 2\n')
    async def admitted(ctx, binding):
        assert binding == json.loads(original['binding'])
        if subsequent == 'access_revoked':
            raise PermissionError('session_revoked')
        with verified_caller(f.actor), verified_invocation(**binding['identity']):
            with queue.admitted_tool(ctx, f.manager, ToolCall(**binding['body']), f.request, mutating=True):
                effects.append('same paid operation')
                queue.current_record('run-1', outcome='completed')
    monkeypatch.setattr(queue, '_dispatch', admitted)
    assert await queue.run_once(f.ctx)
    assert effects == (['same paid operation'] if subsequent == 'success' else [])
    assert record(f)['status'] == ('completed' if subsequent == 'success' else 'blocked')
    assert await queue.reconcile_approved_action_rejections(f.ctx) == 0
    assert not await queue.run_once(f.ctx)


@pytest.mark.asyncio
async def test_unique_truncated_legacy_approval_reference_is_reconciled(fixture, monkeypatch):
    from adaos.services import pending_actions
    f = fixture
    register(f)
    reference = 'pa.runtime_action.' + 'c' * 24
    with queue._connect(queue._database(f.ctx)) as db:
        db.execute("UPDATE continuations SET status='blocked',error=?", (
            "HTTPException: 403: {'error': 'action_approval_required', 'pending_action_id': '" + reference[:-10],))
    async def snapshot(**kwargs): return {'by_id': {reference: approved_action(reference)}}
    monkeypatch.setattr(pending_actions, 'list_pending_actions_async', snapshot)
    assert await queue.reconcile_approved_action_rejections(f.ctx) == 1


@pytest.mark.asyncio
async def test_unavailable_approval_room_does_not_prevent_other_room_wakeup(fixture, monkeypatch):
    from adaos.services import pending_actions
    f = fixture
    register(f)
    first_ref = 'pa.runtime_action.' + 'd' * 24
    second_ref = 'pa.runtime_action.' + 'e' * 24
    with queue._connect(queue._database(f.ctx)) as db:
        original = dict(db.execute('SELECT * FROM continuations').fetchone())
        db.execute("UPDATE continuations SET status='blocked',error=?", (
            "HTTPException: 403: {'error': 'action_approval_required', 'pending_action_id': '" + first_ref + "'}",))
        binding = json.loads(original['binding'])
        binding['body']['context']['webspace_id'] = 'desktop-other'
        db.execute("INSERT INTO continuations(id,owner,key,binding,status,due,expires,error) VALUES(?,?,?,?,?,?,?,?)", (
            'second', original['owner'], 'second', json.dumps(binding), 'blocked', 0, original['expires'],
            "HTTPException: 403: {'error': 'action_approval_required', 'pending_action_id': '" + second_ref + "'}"))
    async def snapshot(**kwargs):
        if kwargs['webspace_id'] == 'desktop-dev': raise RuntimeError('room unavailable')
        return {'by_id': {second_ref: approved_action(second_ref, webspace='desktop-other')}}
    monkeypatch.setattr(pending_actions, 'list_pending_actions_async', snapshot)
    assert await queue.reconcile_approved_action_rejections(f.ctx) == 1
    with queue._connect(queue._database(f.ctx)) as db:
        assert db.execute("SELECT status FROM continuations WHERE id='second'").fetchone()[0] == 'waiting'
    assert record(f)['status'] == 'blocked'


@pytest.mark.asyncio
async def test_optional_approval_wakeup_failure_does_not_stop_regular_worker(fixture, monkeypatch):
    f = fixture
    monkeypatch.setattr(queue, 'reconcile_draining_rejections', lambda ctx: 0)
    monkeypatch.setattr(queue, 'reconcile_equivalent_runtime_rejections', lambda ctx: 0)
    async def unavailable(ctx): raise RuntimeError('approval projection unavailable')
    reached = asyncio.Event()
    async def ordinary(ctx): reached.set()
    monkeypatch.setattr(queue, 'reconcile_approved_action_rejections', unavailable)
    monkeypatch.setattr(queue, 'run_once', ordinary)
    await queue.start(f.ctx)
    try: await asyncio.wait_for(reached.wait(), timeout=2)
    finally: await queue.stop(f.ctx)
