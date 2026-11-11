"""Durable, bounded bootstrap diagnostic delivery, including before enrollment."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import uuid


def durable_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.candidate')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def default_state_dir() -> Path:
    return Path(os.environ.get('ADAOS_BOOTSTRAP_STATE_DIR') or
                str(Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'adaos/bootstrap'))


def flush_outbox(state: Path, *, budget_seconds: float = 10) -> int:
    """Only durable receipts suppress retry. All retries preserve original IDs."""
    pending = 0
    failed_roots: set[str] = set()
    deadline = time.monotonic() + budget_seconds
    for directory in sorted(state.glob('attempt-*'))[:128]:
        root = None
        try:
            if directory.is_symlink():
                continue
            root = (directory / 'root-url').read_text().strip()
            files = [directory / 'request.json', *sorted(directory.glob('event-*.json'))]
            for filename in files:
                if filename.with_suffix('.ack').exists():
                    continue
                if root in failed_roots or time.monotonic() >= deadline:
                    raise TimeoutError('bootstrap delivery budget exhausted')
                if filename.is_symlink() or filename.stat().st_size > 16384:
                    raise ValueError('bootstrap local fact is invalid')
                payload = json.loads(filename.read_text())
                if not isinstance(payload, dict):
                    raise ValueError('bootstrap local fact must be an object')
                endpoint = 'resolve' if filename.name == 'request.json' else 'events'
                request = urllib.request.Request(root + '/v1/bootstrap/' + endpoint,
                    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(request, timeout=min(5, max(0.1, deadline - time.monotonic()))) as response:
                    receipt = json.loads(response.read(16385))
                if not isinstance(receipt, dict):
                    raise ValueError('Root receipt must be an object')
                if receipt.get('durable') is not True or receipt.get('attempt_id', payload['attempt_id']) != payload['attempt_id']:
                    raise ValueError('Root did not confirm durable storage')
                if endpoint == 'events' and receipt.get('event_id') != payload['event_id']:
                    raise ValueError('Root returned another event receipt')
                durable_json(filename.with_suffix('.ack'), {'durable': True})
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            pending += 1
            if root:
                failed_roots.add(root)
    return pending


async def deliver_pending_reports() -> None:
    """API lifespan owns this task; reporting never delays API readiness."""
    while True:
        try:
            await asyncio.to_thread(flush_outbox, default_state_dir())
        except OSError:
            pass  # Preserve local facts; unavailable storage/network is retried.
        await asyncio.sleep(60)
