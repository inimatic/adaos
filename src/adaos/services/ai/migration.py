"""Explicit owner-consented adoption, never a copy of the Chat database."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .chatgpt import AccountError, ChatGPTAccount, KeyringVault


def adopt_legacy(service, *, confirmed=False):
    if not confirmed:
        raise ValueError('ai_core_connection_adoption_requires_confirmation')
    if not isinstance(service.provider, ChatGPTAccount):
        raise ValueError('ai_legacy_adoption_not_supported_by_binding')
    root = getattr(service, 'legacy_root', None)
    if root is None:
        raise ValueError('ai_legacy_source_not_registered_open_context_chat_first')
    root = Path(root).resolve()
    source_path = root / 'files/chatgpt-session.enc'
    if not source_path.is_file():
        raise ValueError('ai_legacy_connection_not_found')
    # The source is pinned by the supervisor. No browser/tool argument can
    # choose a filesystem path or principal to import.
    db_path = root / 'db/context_chat.db'
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as db:
        settings = {key: json.loads(value) for key, value in db.execute(
            "SELECT key,value FROM settings WHERE key IN ('host_id','client_id','subject','account_reauth','inference_verification')")}
    if not settings.get('host_id'):
        raise ValueError('ai_legacy_registration_incomplete')
    legacy = KeyringVault('context-chat:' + settings['host_id'], source_path,
                          service='AdaOS.ContextChat.key')
    credentials = legacy.read()
    if not credentials or not credentials.get('subject') or not credentials.get('client_id'):
        raise AccountError('ai_legacy_credentials_incomplete')
    with service.lock, service.provider.lock:
        if service.provider.pending:
            raise ValueError('ai_sign_in_in_progress_cancel_before_adoption')
        current = service.provider.vault.read()
        if current:
            copied = service.store.setting('legacy_adoption', {})
            if (copied.get('status') != 'copied'
                    or service.provider._registration(current) != service.provider._registration(credentials)):
                raise ValueError('ai_existing_core_connection_will_not_be_overwritten')
            # Cleanup recovery must not roll back a refresh that occurred after
            # the successful Core write but before a failed legacy cleanup.
            legacy.clear()
            service.store.set_setting('legacy_adoption', {'source': 'context_chat', 'status': 'completed'})
            return {'ok': True, 'status': 'adopted', 'history_moved': False}
        service.provider.vault.write(credentials)
        retained = service.provider.vault.read()
        if retained != credentials:
            raise AccountError('ai_core_vault_adoption_verification_failed')
        with service.store.db() as db:
            for key, value in settings.items():
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       ('legacy_adoption', json.dumps({'source': 'context_chat', 'status': 'copied'})))
        service.provider.generation += 1
        service.store.set_setting('generation', service.provider.generation)
        service.provider._models = (0, [])
        # Do not revoke upstream: the same registration now belongs to Core.
        # If cleanup fails the receipt remains copied and a retry is safe.
        legacy.clear()
        service.store.set_setting('legacy_adoption', {'source': 'context_chat', 'status': 'completed'})
    return {'ok': True, 'status': 'adopted', 'history_moved': False}
