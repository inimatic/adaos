"""Retire obsolete Chat credential copies after verified Core ownership adoption.

This compatibility adapter never imports credentials, calls a provider, changes
the active Core account, or interprets conversation records. Ciphertext stays in
private recovery. It runs only inside an admitted, fenced Application cutover.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json, mutation_lock


def _settings(path: Path, keys: tuple[str, ...]) -> dict:
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
        return {key: json.loads(value) for key, value in db.execute(
            'SELECT key,value FROM settings WHERE key IN (' + ','.join('?' for _ in keys) + ')', keys)}


def retire_adopted_legacy_state(owner, lifecycle, *, operation_key: str) -> dict:
    from adaos.services.personalization_runtime import current_user_id

    retired = []
    for component in lifecycle.components:
        name = 'files/chatgpt-session.enc'
        if (component.component_ref != 'skill:context_chat_service' or component.stable_root is None
                or 'ai.inference' not in component.target_manifest.get('capabilities', [])
                or name not in lifecycle.state_file_contracts[component.component_ref]
                or (component.beta_root / name).exists()):
            continue
        source = component.stable_root / name
        area = getattr(lifecycle, 'stable_recovery', lifecycle.recovery) / 'core-owned-state-retirement'
        receipt_path = area / 'chatgpt-session.receipt.json'
        if not source.exists() and not receipt_path.exists():
            continue
        # Core's original, consented adoption copied this host registration.
        # A later sign-in may have changed subject/tokens; never restore them
        # from the Application snapshot or require an old token to be usable.
        authority = getattr(owner, 'authority_context', None) or owner
        principal = 'user:' + current_user_id(authority)
        state = Path(authority.paths.state_dir()).resolve()
        account = state / 'ai' / hashlib.sha256(principal.encode()).hexdigest()
        core = _settings(account / 'ai.sqlite3', ('host_id', 'legacy_adoption'))
        original = _settings(component.stable_root / 'db/context_chat.db', ('host_id',))
        adoption = core.get('legacy_adoption') or {}
        if (adoption.get('source') != 'context_chat' or adoption.get('status') != 'completed'
                or not core.get('host_id') or core['host_id'] != original.get('host_id')
                or not (account / 'connection.enc').is_file()):
            raise ValueError('Legacy credential retirement requires verified Core adoption of the same host')
        if source.absolute() != source.resolve():
            raise ValueError('Linked legacy credential state cannot be retired')
        with mutation_lock(receipt_path.with_suffix('.lock')):
            backup = area / 'chatgpt-session.enc'
            identity = {'schema': 'adaos.ai.legacy_state_retirement.v1',
                'application_id': lifecycle.application_id, 'candidate_id': lifecycle.candidate_id,
                'release_digest': lifecycle.release_digest, 'operation_key': operation_key,
                'source': str(source), 'credential_owner': principal}
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
                if receipt.get('identity') != identity:
                    raise ValueError('Retained credential retirement identity changed')
                payload = backup.read_bytes()
            else:
                if source.stat().st_size > 262144:
                    raise ValueError('Legacy ciphertext exceeds the declared state-file bound')
                payload = source.read_bytes()
                if backup.exists() and backup.read_bytes() != payload:
                    raise ValueError('Retained legacy ciphertext changed')
                atomic_write_bytes(backup, payload)
                receipt = {'identity': identity, 'digest': hashlib.sha256(payload).hexdigest(), 'status': 'prepared'}
                atomic_write_json(receipt_path, receipt)
            if hashlib.sha256(payload).hexdigest() != receipt['digest']:
                raise ValueError('Retained legacy ciphertext digest changed')
            if source.exists():
                if source.read_bytes() != payload:
                    raise ValueError('Legacy ciphertext changed during retirement')
                source.unlink()
            atomic_write_json(receipt_path, {**receipt, 'status': 'retired'})
            retired.append(component.component_ref)
    return {'ok': True, 'retired_components': retired, 'core_connection_unchanged': True}
