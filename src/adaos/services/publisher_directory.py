"""Mutable publisher presentation keyed by immutable subnet identity.

Profiles do not grant publication rights or replace release signatures. The
local subnet name remains node-identity-owned; release snapshots are fallbacks.
"""
from __future__ import annotations

from typing import Any, Mapping

from adaos.adapters.db.sqlite import durable_state_get, durable_state_put
from adaos.services.subnet_alias import load_subnet_alias

_NAMESPACE = 'publisher_directory'


def _local_ref(owner: Any) -> str:
    config = getattr(owner, 'config', None)
    subnet = str(getattr(config, 'subnet_id_value', None) or getattr(config, 'subnet_id', None) or '').strip()
    return subnet if subnet.startswith('subnet:') else 'subnet:' + subnet if subnet else ''


class PublisherDirectory:
    """One read/observation scope; never keep this object as a global cache."""

    def __init__(self, owner: Any):
        self.owner = getattr(owner, 'authority_context', None) or owner
        self._local_name: str | None = None

    def refresh_local(self) -> dict:
        self._local_name = None
        ref = _local_ref(self.owner)
        if not ref:
            return {}
        name = load_subnet_alias(subnet_id=ref.removeprefix('subnet:')) or ref.removeprefix('subnet:')
        identity = {'publisher_ref': ref, 'subnet_id': ref.removeprefix('subnet:'), 'display_name': name,
                    'source': 'local_subnet_identity'}
        previous = durable_state_get(_NAMESPACE, ref) or {}
        if all(previous.get(k) == v for k, v in identity.items()):
            return previous
        profile = {**identity, 'revision': int(previous.get('revision') or 0) + 1}
        durable_state_put(_NAMESPACE, ref, profile)
        return profile

    def resolve(self, publisher_ref: str, snapshot: Mapping[str, Any] | None = None) -> dict:
        ref = str(publisher_ref or '')
        presentation = dict(snapshot or {})
        if not ref.startswith('subnet:'):
            return presentation
        presentation['publisher_ref'] = ref
        if ref == _local_ref(self.owner):
            # A lost invalidation cannot keep a local rename stale. This read
            # never rewrites Application/release metadata or signs a profile.
            if self._local_name is None:
                self._local_name = load_subnet_alias(subnet_id=ref.removeprefix('subnet:')) or ref.removeprefix('subnet:')
            presentation['display_name'] = self._local_name
            return presentation
        try:
            profile = durable_state_get(_NAMESPACE, ref) or {}
        except (RuntimeError, OSError, ValueError):
            profile = {}
        if profile.get('publisher_ref') == ref and profile.get('display_name'):
            presentation['display_name'] = profile['display_name']
        presentation.setdefault('display_name', ref.removeprefix('subnet:'))
        return presentation
