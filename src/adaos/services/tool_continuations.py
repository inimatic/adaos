"""Core-owned, bounded replay of one admitted, idempotent skill invocation.

The queue retains identity, never credentials or an application-provided caller.
Every attempt crosses the ordinary ingress gates again. Applications must persist
their effect/job receipts before submitting effects: delivery is at least once.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator
import uuid

_log = logging.getLogger(__name__)
_ADMITTED: ContextVar[dict | None] = ContextVar("admitted_continuation_call", default=None)
_REPLAY: ContextVar[dict | None] = ContextVar("core_continuation_replay", default=None)
_RUNNERS: dict[str, asyncio.Task] = {}


def is_replay() -> bool:
    return _REPLAY.get() is not None


def _database(ctx: Any) -> Path:
    return Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir()) / "tool-continuations.sqlite3"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS continuations (
        id TEXT PRIMARY KEY, owner TEXT NOT NULL, key TEXT NOT NULL,
        binding TEXT NOT NULL, status TEXT NOT NULL, due REAL NOT NULL,
        expires REAL NOT NULL, lease TEXT, lease_until REAL,
        attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
        UNIQUE(owner,key))""")
    return db


def _runtime_pin(manager: Any, skill: str, dev: bool) -> dict:
    status = manager.dev_runtime_status(skill) if dev else manager.runtime_status(skill)
    manifest = Path(status["resolved_manifest"]).resolve()
    return _manifest_pin(manifest)


def _manifest_pin(manifest: Path, *, normalize_slot: bool = False) -> dict:
    raw = manifest.read_bytes()
    descriptor = json.loads(raw)
    source = Path(descriptor["source"]).resolve()
    if normalize_slot:
        root = str(descriptor.get("slot_root") or "")
        def normalized(value):
            if isinstance(value, dict): return {k:normalized(v) for k,v in value.items()}
            if isinstance(value, list): return [normalized(v) for v in value]
            return value.replace(root,"<SLOT>") if root and isinstance(value,str) else value
        descriptor = normalized(descriptor)
        if root: descriptor["slot"] = "<SLOT>"
        raw = json.dumps(descriptor,sort_keys=True,separators=(",",":")).encode()
    digest = hashlib.sha256(raw)
    # Runtime data and caches are deliberately excluded; imported code and
    # configuration are pinned even when the generated manifest did not change.
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if any(p in {"__pycache__", ".git", "tests", "data", "state"} for p in relative.parts):
            continue
        if item.is_file() and item.suffix.lower() in {".py", ".json", ".yaml", ".yml"}:
            digest.update(relative.as_posix().encode())
            digest.update(item.read_bytes())
    return {"manifest": str(manifest), "source": str(source), "digest": digest.hexdigest()}


def _same_runtime(expected: dict, current: dict) -> bool:
    if expected == current: return True
    try:
        # Re-read both immutable candidates. Relocation is admitted only when
        # the original cryptographic pin is still intact and every code/config
        # byte agrees after substituting slot-owned paths, never versions/rights.
        for pin in (expected,current):
            if _manifest_pin(Path(pin["manifest"])) != pin: return False
        return _manifest_pin(Path(expected["manifest"]),normalize_slot=True)["digest"] == _manifest_pin(Path(current["manifest"]),normalize_slot=True)["digest"]
    except (OSError,ValueError,KeyError):
        return False


def _without_approval(value: dict) -> dict:
    """Do not replay a one-time approval as fresh human authorization."""
    result = json.loads(json.dumps(value))
    containers = [result, result.get("context", {}), result.get("arguments", {})]
    for container in list(containers):
        if isinstance(container, dict) and isinstance(container.get("_meta"), dict):
            containers.append(container["_meta"])
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("action_approval", "approval", "approval_ref", "pending_action_approval",
                    "pending_action_id", "pending_action_status", "response_action_id"):
            container.pop(key, None)
        container.pop("_verified_application_access", None)
        if container.get("action_source") == "operator_ui":
            container["action_source"] = "core_continuation"
    return result


@contextmanager
def admitted_tool(ctx: Any, manager: Any, body: Any, request: Any, *, mutating: bool) -> Iterator[None]:
    """Bind only after admission; called by Core around the actual local dispatch."""
    from adaos.services.policy.caller import current_caller, current_caller_scope
    from adaos.services.policy.invocation import current_invocation

    actor = current_caller()
    skill, tool = body.tool.split(":", 1)
    replay = _REPLAY.get()
    if replay is not None and not _same_runtime(replay["pin"], _runtime_pin(manager, skill, body.dev)):
        raise PermissionError("continuation_runtime_changed")
    admission = {
        "ctx": ctx, "manager": manager, "body": body, "request": request,
        "actor": actor, "scope": current_caller_scope(), "identity": current_invocation(),
        "skill": skill, "tool": tool, "mutating": mutating, "armed": [],
    }
    marker = _ADMITTED.set(admission)
    try:
        yield
    finally:
        # Do not let the background attempt race the foreground call that
        # registered it. A process crash recovers armed work after its lease.
        if admission["armed"]:
            with _connect(_database(ctx)) as db:
                for job_id in admission["armed"]:
                    db.execute("UPDATE continuations SET status='waiting',lease_until=NULL WHERE id=? AND status='armed'", (job_id,))
        _ADMITTED.reset(marker)


def defer_current(*, key: str, retry_after_s: float, ttl_s: float) -> dict:
    admission = _ADMITTED.get()
    if not admission or not admission["mutating"] or admission["actor"] is None:
        raise PermissionError("continuation_admitted_mutation_required")
    from adaos.sdk.access import require
    require("workspace.write")
    identity = admission["identity"] or {}
    if not identity.get("request_id") or not identity.get("idempotency_key"):
        raise PermissionError("continuation_stable_invocation_required")
    if not isinstance(key, str) or not key.strip() or len(key) > 256:
        raise ValueError("continuation_key_invalid")
    interval = float(retry_after_s)
    ttl = float(ttl_s)
    if not 1 <= interval <= 60 or not 10 <= ttl <= 7200:
        raise ValueError("continuation_interval_or_ttl_invalid")
    manager, body = admission["manager"], admission["body"]
    status = manager.dev_runtime_status(admission["skill"]) if body.dev else manager.runtime_status(admission["skill"])
    descriptor = json.loads(Path(status["resolved_manifest"]).read_text(encoding="utf-8"))
    if descriptor.get("tools", {}).get(admission["tool"], {}).get("durable_replay") is not True:
        raise PermissionError("continuation_tool_must_declare_durable_replay")
    actor = admission["actor"].to_dict()
    scope = admission["scope"]
    owner = json.dumps([actor, admission["skill"]], sort_keys=True)
    headers = admission["request"].headers
    request_state = admission["request"].state
    pin = _runtime_pin(manager, admission["skill"], body.dev)
    replay = _REPLAY.get()
    if replay is not None and _same_runtime(replay["pin"],pin):
        pin = replay["pin"]  # Keep the original frozen binding across a slot copy.
    binding = {
        "actor": actor, "scope": scope.to_dict() if scope else None,
        "identity": identity, "body": _without_approval(body.model_dump(mode="json")),
        "pin": pin,
        "headers": {k: headers[k] for k in ("x-adaos-route",) if k in headers},
        "ingress": {
            "root_routed": bool(getattr(request_state, "adaos_root_routed", False)),
            "development_access": bool(getattr(request_state, "adaos_development_access", False)),
        },
        "interval": interval,
    }
    encoded = json.dumps(binding, sort_keys=True, separators=(",", ":"))
    now = time.time()
    job_id = "continuation." + uuid.uuid4().hex
    with _connect(_database(admission["ctx"])) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM continuations WHERE owner=? AND key=?", (owner, key)).fetchone()
        if row:
            old = json.loads(row["binding"])
            # A transport retry may refresh its deadline, but cannot rebind the
            # same operation to another mask, caller, runtime, or invocation.
            if old != binding:
                raise ValueError("continuation_binding_conflict")
            return _receipt(row)
        db.execute("INSERT INTO continuations(id,owner,key,binding,status,due,expires,lease_until) VALUES(?,?,?,?,?,?,?,?)",
                   (job_id, owner, key, encoded, "armed", now + interval, now + ttl, now + 600))
        row = db.execute("SELECT * FROM continuations WHERE id=?", (job_id,)).fetchone()
    admission["armed"].append(job_id)
    return _receipt(row)


def _receipt(row: Any) -> dict:
    return {"id": row["id"], "key": row["key"], "status": row["status"],
            "attempts": row["attempts"], "expires_at": row["expires"], "error": row["error"]}


def current_record(key: str, *, outcome: str | None = None) -> dict | None:
    from adaos.sdk.access import require
    require("workspace.write" if outcome else "workspace.read")
    admission = _ADMITTED.get()
    if not admission or admission["actor"] is None:
        raise PermissionError("continuation_admitted_call_required")
    if outcome and not admission["mutating"]:
        raise PermissionError("continuation_admitted_mutation_required")
    owner = json.dumps([admission["actor"].to_dict(), admission["skill"]], sort_keys=True)
    with _connect(_database(admission["ctx"])) as db:
        row = db.execute("SELECT * FROM continuations WHERE owner=? AND key=?", (owner, key)).fetchone()
        if row is None:
            return None
        if outcome:
            if outcome not in {"completed", "blocked", "cancelled"}:
                raise ValueError("continuation_outcome_invalid")
            if row["status"] not in {"completed", "blocked", "cancelled", "expired"}:
                db.execute("UPDATE continuations SET status=?,lease=NULL,lease_until=NULL WHERE id=?", (outcome, row["id"]))
                row = db.execute("SELECT * FROM continuations WHERE id=?", (row["id"],)).fetchone()
        return _receipt(row)


def _claim(path: Path, now: float) -> dict | None:
    with _connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE continuations SET status='expired',error='authorization_window_expired' WHERE expires<=? AND status IN ('waiting','running','armed')", (now,))
        row = db.execute("SELECT * FROM continuations WHERE expires>? AND ((status='waiting' AND due<=?) OR (status IN ('running','armed') AND lease_until<=?)) ORDER BY due LIMIT 1", (now, now, now)).fetchone()
        if row is None:
            return None
        lease = uuid.uuid4().hex
        db.execute("UPDATE continuations SET status='running',lease=?,lease_until=?,attempts=attempts+1 WHERE id=?", (lease, now + 120, row["id"]))
        return {**dict(row), "lease": lease}


async def run_once(ctx: Any) -> bool:
    """Claim one durable intent, re-admit it and reconcile the same operation."""
    path = _database(ctx)
    row = await asyncio.to_thread(_claim, path, time.time())
    if row is None:
        return False
    binding = json.loads(row["binding"])
    marker = _REPLAY.set(binding)
    failure = None
    deferred_by_lifecycle = False
    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(30)
            with _connect(path) as db:
                db.execute("UPDATE continuations SET lease_until=? WHERE id=? AND status='running' AND lease=?",
                           (time.time() + 120, row["id"], row["lease"]))
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        await _dispatch(ctx, binding)
    except asyncio.CancelledError:
        # A crash or shutdown leaves the lease for bounded recovery. The app
        # reconciles its durable effect receipts rather than submitting anew.
        raise
    except Exception as exc:
        failure = f"{type(exc).__name__}: {str(exc)[:500]}"
        approval = _action_approval_rejection(exc)
        if approval:
            # Keep the complete pending-action reference. The generic error
            # excerpt can truncate it after the (large) policy explanation.
            failure = "HTTPException: 403: " + repr(approval)
        deferred_by_lifecycle = _is_draining_rejection(exc)
        _log.warning("continuation %s id=%s error=%s", "deferred" if deferred_by_lifecycle else "blocked", row["id"], failure)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        _REPLAY.reset(marker)
    with _connect(path) as db:
        db.execute("UPDATE continuations SET status=?,due=?,lease=NULL,lease_until=NULL,error=? WHERE id=? AND status='running' AND lease=?",
                   ("blocked" if failure and not deferred_by_lifecycle else "waiting", time.time() + binding["interval"], failure, row["id"], row["lease"]))
    return True


def _is_draining_rejection(exc: Exception) -> bool:
    from fastapi import HTTPException
    return (isinstance(exc, HTTPException) and exc.status_code == 503
            and isinstance(exc.detail, dict) and exc.detail.get("error") == "node_draining"
            and exc.detail.get("retryable") is True)


def _action_approval_rejection(exc: Exception) -> dict | None:
    from fastapi import HTTPException
    if not (isinstance(exc, HTTPException) and exc.status_code == 403
            and isinstance(exc.detail, dict)
            and exc.detail.get("error") == "action_approval_required"):
        return None
    reference = str(exc.detail.get("pending_action_id") or "")
    if not re.fullmatch(r"pa\.runtime_action\.[0-9a-f]{24}", reference):
        return None
    return {"error": "action_approval_required", "pending_action_id": reference}


async def reconcile_approved_action_rejections(ctx: Any) -> int:
    """Wake frozen work after its recorded approval, through normal re-admission.

    This restores scheduling only: caller, rights, runtime pin and effect
    receipts are checked again by _dispatch. Pending/refused approvals, expired
    work and unrelated errors never retry. No one-time approval is replayed.
    """
    from adaos.services.pending_actions import list_pending_actions_async
    from adaos.services.yjs.webspace import default_webspace_id

    path = _database(ctx)
    with _connect(path) as db:
        rows = db.execute("SELECT id,binding,error FROM continuations WHERE status='blocked' AND expires>? AND error LIKE 'HTTPException: 403: %action_approval_required%' LIMIT 1000", (time.time(),)).fetchall()
    snapshots: dict[str, dict] = {}
    restored = 0
    for row in rows:
        match = re.search(r"['\"]pending_action_id['\"]\s*:\s*['\"](pa\.runtime_action\.[0-9a-f]{12,24})", row["error"])
        if not match:
            continue
        binding = json.loads(row["binding"])
        body = binding["body"]
        arguments = body.get("arguments") or {}
        context = body.get("context") or {}
        webspace = str(arguments.get("webspace_id") or (arguments.get("_meta") or {}).get("webspace_id")
                       or context.get("webspace_id") or default_webspace_id())
        if webspace not in snapshots:
            try:
                snapshots[webspace] = await list_pending_actions_async(webspace_id=webspace, include_terminal=True)
            except Exception:
                # An unavailable room must not strand work in other rooms or
                # prevent the ordinary continuation worker from making progress.
                _log.warning("approval wakeup snapshot unavailable webspace=%s", webspace, exc_info=True)
                snapshots[webspace] = {"by_id": {}}
        # Older error excerpts retained only a prefix. A unique match is safe
        # to schedule; ingress must still validate the complete exact binding.
        candidates = [action for ident, action in snapshots[webspace].get("by_id", {}).items()
                      if ident.startswith(match[1]) and re.fullmatch(r"pa\.runtime_action\.[0-9a-f]{24}", ident)]
        if len(candidates) != 1:
            continue
        action = candidates[0]
        domain = action.get("domain_ref") or {}
        if (action.get("kind") != "runtime.action_approval" or action.get("status") != "responded"
                or (action.get("response") or {}).get("response_action_id") != "approve"
                or domain.get("tool") != body["tool"] or domain.get("webspace_id") != webspace):
            continue
        with _connect(path) as db:
            restored += db.execute("UPDATE continuations SET status='waiting',due=?,error=NULL WHERE id=? AND status='blocked' AND expires>? AND binding=?",
                                   (time.time(), row["id"], time.time(), row["binding"])).rowcount
    return restored


def reconcile_draining_rejections(ctx: Any) -> int:
    """Recover only old pre-dispatch lifecycle refusals, with the same binding.

    Rights, runtime pins and effect receipts are rechecked by normal dispatch.
    No authorization failure or ambiguous execution is admitted here.
    """
    import ast
    from fastapi import HTTPException
    count = 0
    with _connect(_database(ctx)) as db:
        rows = db.execute("SELECT id,error FROM continuations WHERE status='blocked' AND expires>? AND error LIKE 'HTTPException: 503: %' LIMIT 1000", (time.time(),)).fetchall()
        for row in rows:
            try:
                detail = ast.literal_eval(row["error"][len("HTTPException: 503: "):])
            except (ValueError, SyntaxError):
                continue
            if not _is_draining_rejection(HTTPException(503, detail)):
                continue
            count += db.execute("UPDATE continuations SET status='waiting',due=?,lease=NULL,lease_until=NULL WHERE id=? AND status='blocked'", (time.time(), row["id"])).rowcount
    return count


def reconcile_equivalent_runtime_rejections(ctx: Any) -> int:
    """Re-admit retained identical slot copies; substantive updates stay blocked."""
    from adaos.services.skill.manager import SkillManager
    count = 0
    with _connect(_database(ctx)) as db:
        rows = db.execute("SELECT id,binding FROM continuations WHERE status='blocked' AND expires>? AND (error LIKE '%continuation_runtime_changed%' OR error='RuntimeError: continuation_tool_failed:continuation_binding_conflict') LIMIT 1000",(time.time(),)).fetchall()
    if not rows: return 0
    from adaos.adapters.db import SqliteSkillRegistry
    manager = SkillManager(repo=ctx.skills_repo,registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,paths=ctx.paths,bus=getattr(ctx,"bus",None),caps=ctx.caps,settings=ctx.settings)
    for row in rows:
        binding = json.loads(row["binding"]);body=binding["body"]
        skill = body["tool"].split(":",1)[0]
        try:
            equivalent = _same_runtime(binding["pin"],_runtime_pin(manager,skill,bool(body.get("dev"))))
        except Exception:
            _log.warning("continuation runtime reconciliation unavailable id=%s",row["id"],exc_info=True)
            continue
        if not equivalent: continue
        with _connect(_database(ctx)) as db:
            count += db.execute("UPDATE continuations SET status='waiting',due=?,lease=NULL,lease_until=NULL WHERE id=? AND status='blocked'",(time.time(),row["id"])).rowcount
    return count


def operator_readmit_runtime(
    ctx: Any, manager: Any, continuation_id: str, *, expected_digest: str,
    target_digest: str, operator: str, approval_ref: str, reason: str, apply: bool = False,
    renew_ttl_s: int = 0, expected_expiry: float | None = None,
) -> dict:
    """Local administrator recovery of one frozen intent after a code update.

    This is deliberately absent from the application SDK and HTTP ingress.
    The local CLI operator must explicitly authorize the reviewed target hash.
    Every normal caller, approval and capability gate still runs on replay.
    No arguments, actor, effect identity or application data change. The deadline
    is preserved unless the operator separately authorizes a bounded renewal
    with an exact comparison against the previously reviewed expiry.
    """
    if not all(isinstance(v, str) and v.strip() for v in (operator, approval_ref, reason)):
        raise ValueError('continuation_readmission_authority_required')
    if len(reason) > 2000 or len(approval_ref) > 500 or len(operator) > 200:
        raise ValueError('continuation_readmission_authority_too_long')
    if not all(re.fullmatch('[0-9a-f]{64}', v) for v in (expected_digest, target_digest)):
        raise ValueError('continuation_readmission_digest_required')
    if type(renew_ttl_s) is not int or not 0 <= renew_ttl_s <= 7200:
        raise ValueError('continuation_readmission_renewal_invalid')
    if renew_ttl_s and (type(expected_expiry) not in {int, float} or not math.isfinite(expected_expiry)):
        raise ValueError('continuation_readmission_expected_expiry_required')
    with _connect(_database(ctx)) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM continuations WHERE id=?', (continuation_id,)).fetchone()
        if row is None:
            raise ValueError('continuation_not_found')
        binding = json.loads(row['binding'])
        body = binding['body'];skill, tool = body['tool'].split(':', 1)
        dev = bool(body.get('dev'))
        status = manager.dev_runtime_status(skill) if dev else manager.runtime_status(skill)
        pin = _runtime_pin(manager, skill, dev)
        if pin['digest'] != target_digest:
            raise ValueError('continuation_target_runtime_changed')
        manifest = json.loads(Path(pin['manifest']).read_text(encoding='utf-8'))
        if (not status.get('ready', True) or status.get('deactivated')
                or manifest.get('name') != skill
                or manifest.get('tools', {}).get(tool, {}).get('durable_replay') is not True):
            raise PermissionError('continuation_target_not_replayable')
        has_audit = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='continuation_readmissions'").fetchone()
        previous = (db.execute('SELECT * FROM continuation_readmissions WHERE continuation_id=? AND new_digest=? AND approval_ref=?',
                              (continuation_id, target_digest, approval_ref)).fetchone() if has_audit else None)
        if previous is not None and previous['old_digest'] == expected_digest and binding['pin'] == pin:
            if renew_ttl_s and (dict(previous).get('expires_before') != expected_expiry or dict(previous).get('renew_ttl_s') != renew_ttl_s):
                raise ValueError('continuation_readmission_renewal_conflict')
            return {'ok': True, 'applied': True, 'idempotent': True, 'receipt': dict(previous)}
        if binding['pin']['digest'] != expected_digest:
            raise ValueError('continuation_expected_runtime_changed')
        if (row['status'] not in ({'blocked','expired'} if renew_ttl_s else {'blocked'})
                or (row['expires'] <= time.time() and not renew_ttl_s)
                or not any(s in (row['error'] or '') for s in ('continuation_runtime_changed', 'continuation_binding_conflict'))):
            raise ValueError('continuation_not_runtime_blocked')
        if renew_ttl_s and row['expires'] != expected_expiry:
            raise ValueError('continuation_readmission_expiry_changed')
        amended = {**binding, 'pin': pin}
        encoded = json.dumps(amended, sort_keys=True, separators=(',', ':'))
        receipt = {'id': 'readmission.' + uuid.uuid4().hex, 'continuation_id': continuation_id,
                   'old_digest': expected_digest, 'new_digest': target_digest,
                   'operator': operator, 'approval_ref': approval_ref, 'reason': reason,
                   'created_at': time.time(),
                   'before_sha256': hashlib.sha256(row['binding'].encode()).hexdigest(),
                   'after_sha256': hashlib.sha256(encoded.encode()).hexdigest(),
                   'expires_before':row['expires'], 'expires_after':time.time()+renew_ttl_s if renew_ttl_s else row['expires'],
                   'renew_ttl_s':renew_ttl_s}
        if apply:
            db.execute('''CREATE TABLE IF NOT EXISTS continuation_readmissions (
                id TEXT PRIMARY KEY, continuation_id TEXT NOT NULL, old_digest TEXT NOT NULL,
                new_digest TEXT NOT NULL, operator TEXT NOT NULL, approval_ref TEXT NOT NULL,
                reason TEXT NOT NULL, created_at REAL NOT NULL, before_sha256 TEXT NOT NULL,
                after_sha256 TEXT NOT NULL, UNIQUE(continuation_id,new_digest,approval_ref))''')
            columns={c['name'] for c in db.execute('PRAGMA table_info(continuation_readmissions)')}
            for name,kind in (('expires_before','REAL'),('expires_after','REAL'),('renew_ttl_s','INTEGER')):
                if name not in columns:db.execute(f'ALTER TABLE continuation_readmissions ADD COLUMN {name} {kind}')
            db.execute('INSERT INTO continuation_readmissions ('+','.join(receipt)+') VALUES('+','.join('?' for _ in receipt)+')',tuple(receipt.values()))
            db.execute("UPDATE continuations SET binding=?,status='waiting',due=?,expires=?,lease=NULL,lease_until=NULL,error=NULL WHERE id=?",
                       (encoded, time.time(), receipt['expires_after'], continuation_id))
    if apply:
        _log.warning('Operator re-admitted frozen continuation id=%s old=%s new=%s operator=%s approval=%s',
                     continuation_id, expected_digest, target_digest, operator, approval_ref)
    return {'ok': True, 'applied': apply, 'receipt': receipt}


async def _dispatch(ctx: Any, binding: dict) -> None:
    from starlette.requests import Request
    from starlette.responses import Response
    from adaos.apps.api.tool_bridge import ToolCall, call_tool
    from adaos.domain.personalization_access import SubjectRef, ScopeRef

    request = Request({"type": "http", "method": "POST", "path": "/api/tools/call",
                       "headers": [(k.encode(), v.encode()) for k, v in binding["headers"].items()]})
    request.state.adaos_verified_caller = SubjectRef(**binding["actor"])
    request.state.adaos_verified_caller_scope = ScopeRef(**binding["scope"]) if binding["scope"] else None
    request.state.adaos_root_routed = bool(binding.get("ingress", {}).get("root_routed"))
    request.state.adaos_development_access = bool(binding.get("ingress", {}).get("development_access"))
    # This is a Core-owned replay, not new owner credentials. call_tool rechecks
    # scoped caller, application grants, action approval, runtime, and lifecycle.
    result = await call_tool(ToolCall(**binding["body"]), request, Response(), ctx)
    for receipt in (result, result.get("result") if isinstance(result, dict) else None):
        if isinstance(receipt, dict) and receipt.get("ok") is False:
            raise RuntimeError("continuation_tool_failed:" + str(receipt.get("error") or "rejected")[:200])


async def start(ctx: Any) -> None:
    key = str(_database(ctx).resolve())
    if key in _RUNNERS and not _RUNNERS[key].done():
        return
    await asyncio.to_thread(reconcile_draining_rejections, ctx)
    await asyncio.to_thread(reconcile_equivalent_runtime_rejections, ctx)

    async def loop() -> None:
        approval_check_at = 0.0
        while True:
            try:
                if time.monotonic() >= approval_check_at:
                    try:
                        await reconcile_approved_action_rejections(ctx)
                    except Exception:
                        _log.warning("approval wakeup reconciliation failed", exc_info=True)
                    approval_check_at = time.monotonic() + 5
                await run_once(ctx)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("continuation runner iteration failed")
            await asyncio.sleep(1)

    # Do not inherit an HTTP caller or application context into the service.
    from contextvars import Context
    _RUNNERS[key] = asyncio.create_task(loop(), context=Context(), name="tool-continuations")


async def stop(ctx: Any) -> None:
    task = _RUNNERS.pop(str(_database(ctx).resolve()), None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
