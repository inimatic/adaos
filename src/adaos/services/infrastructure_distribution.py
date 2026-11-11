"""Node-admitted bootstrap controller; Applications never select commands or keys."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

MAX_BYTES = 1024 * 1024
_LOCK = threading.RLock()
_CACHE = {}
# Project public metadata only, including data returned by a regional Root.
_FIELDS = frozenset('''ok schema error observed_at batches publications roots history_limit activation_enabled
    id batch_id profile_id state updated_at actor source_revision channel zone_id release result plan_hash created_at
    plan_sha256 plan matrix_sha256 items ready activates_channel profile reason publication bucket key installer
    release_id os arch libc min_libc python features planned bundle_url sha256 bytes source revision candidate
    python_version uv_version adaos_version built_at url catalogue releases verified_at active reconciled demand configurations
    total truncated activity attempts events failure_count limit failures failures_truncated attempt_id event_id type
    stage route status received_at distribution distribution_version libc_version profile_key requested_release
    selected_release first_seen last_seen fallback_succeeded fallback_failed replayed worker last_poll outcome'''.split())


class InfrastructureDistributionError(RuntimeError):
    pass


def _project(value, depth=0):
    if depth > 12:
        raise InfrastructureDistributionError('controller_payload_invalid')
    if isinstance(value, dict):
        return {key: _project(item, depth + 1) for key, item in value.items() if key in _FIELDS}
    if isinstance(value, list):
        if len(value) > 100:
            raise InfrastructureDistributionError('controller_payload_budget')
        return [_project(item, depth + 1) for item in value]
    if isinstance(value, str) and len(value) <= 2048:
        return value
    if value is None or type(value) in (bool, int, float):
        return value
    raise InfrastructureDistributionError('controller_payload_invalid')


def _admission(ctx):
    state = Path(getattr(ctx, 'authority_state_dir', None) or ctx.paths.state_dir())
    path = state / 'private_recovery/bootstrap-delivery/provider.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32768:
        raise InfrastructureDistributionError('distribution_provider_not_configured')
    config = json.loads(path.read_text(encoding='utf-8'))
    skill = getattr(ctx.skill_ctx.get(), 'name', '')
    if config.get('schema') != 'adaos.infrastructure.distribution-admission.v1' or skill not in config.get('allowed_skills', []):
        raise PermissionError('distribution_provider_admission_denied')
    # Pin the controller and all executable sibling dependencies, not just its
    # entry point. An admitted source checkout is not an arbitrary shell runner.
    files = config.get('controller_files', {})
    controller = Path(config['controller'])
    if controller.name not in files or len(files) > 8:
        raise InfrastructureDistributionError('distribution_controller_not_pinned')
    for name, expected in files.items():
        source = controller.parent / name
        if Path(name).name != name or source.is_symlink() or not source.is_file() or source.stat().st_size > 2 * 1024 * 1024:
            raise InfrastructureDistributionError('distribution_controller_changed')
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise InfrastructureDistributionError('distribution_controller_changed')
    required = {'bootstrap-delivery-controller.py', 'bootstrap-release-regru.py', 'public-site-regru.py', 'infra-bootstrap.py'}
    if not required.issubset(files):
        raise InfrastructureDistributionError('distribution_controller_not_pinned')
    for name in ('python', 'config_file'):
        source = Path(config[name])
        if source.is_symlink() or not source.is_file():
            raise InfrastructureDistributionError('distribution_provider_unavailable')
    return state, config


def _invoke(state, config, action, payload=None):
    temporary = state / 'provider-tmp/bootstrap-delivery'
    temporary.mkdir(parents=True, exist_ok=True)
    environment = {key: value for key, value in os.environ.items() if key.upper() in
                   {'PATH', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'}}
    environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    # Files bound stdout without PIPE deadlock, including a misbehaving producer.
    with tempfile.TemporaryFile(dir=temporary) as output:
        process = subprocess.Popen([config['python'], '-I', '-B', config['controller'], action, '--config', config['config_file']],
            stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL, env=environment,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            process.stdin.write(json.dumps(payload or {}).encode())
            process.stdin.close()
            deadline = time.monotonic() + 20
            while process.poll() is None:
                if time.monotonic() > deadline or output.tell() > MAX_BYTES:
                    raise InfrastructureDistributionError('distribution_controller_budget')
                time.sleep(.03)
            if output.tell() > MAX_BYTES:
                raise InfrastructureDistributionError('distribution_controller_unavailable')
            output.seek(0)
            result = _project(json.loads(output.read(MAX_BYTES + 1)))
            # Also reject NaN/Infinity before admitting a snapshot to the UI.
            json.dumps(result, allow_nan=False)
            if not isinstance(result, dict):
                raise ValueError()
            if process.returncode and result.get('ok') is not False:
                raise ValueError()
            return result
        except (ValueError, TypeError):
            raise InfrastructureDistributionError('distribution_controller_payload_invalid') from None
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


def distribution_snapshot(ctx):
    with _LOCK:
        state, config = _admission(ctx)  # Admission is rechecked even on a cache hit.
        key = str(state), hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        previous = _CACHE.get(key)
        if previous and time.monotonic() - previous[0] < 30:
            return _with_permissions(previous[1], config)
        result = _invoke(state, config, 'snapshot')
        if result.get('ok') is not True:
            raise InfrastructureDistributionError('distribution_controller_unavailable')
        _CACHE[key] = time.monotonic(), result
        return _with_permissions(result, config)


def _with_permissions(result, config):
    from adaos.sdk import access
    actor = access.caller()
    allowed = bool(actor and actor['kind'] + ':' + actor['id'] in config.get('allowed_publishers', []))
    if allowed:
        try:
            access.require('workspace.write')
        except Exception:
            allowed = False
    return {**json.loads(json.dumps(result)), 'can_publish': allowed}


def publish_distribution(ctx, expected_plan_sha256):
    from adaos.sdk import access
    import re
    access.require('workspace.write')
    actor, invocation = access.caller(), access.invocation()
    if not actor or not invocation or not invocation.get('request_id'):
        raise PermissionError('verified_publication_invocation_required')
    if not isinstance(expected_plan_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_plan_sha256):
        raise ValueError('invalid_publication_plan')
    with _LOCK:
        state, config = _admission(ctx)
        subject = actor['kind'] + ':' + actor['id']
        if subject not in config.get('allowed_publishers', []):
            raise PermissionError('publication_actor_not_admitted')
        request_id = hashlib.sha256(str(invocation['request_id']).encode()).hexdigest()
        result = _invoke(state, config, 'submit', {'expected_plan_sha256': expected_plan_sha256,
                                                 'request_id': request_id, 'actor': subject})
        _CACHE.clear()
        return result
