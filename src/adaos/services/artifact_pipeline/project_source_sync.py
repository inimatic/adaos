"""Core-owned, restartable complete-Project source synchronization.

The existing checkpoint-intent owner retains source independently of packages.
Transport reads are proof reads from the remote retention ref, never Redis HEAD.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import re
from typing import Any, Protocol
from uuid import uuid4

import yaml

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.development_source_admission import (
    SourceAdmissionError, assert_project_ownership, project_owned_refs, source_write_admission, ownership_inventory,
)
from .source_snapshots import SourceSnapshot, SourceSnapshotError
from .storage import atomic_write_bytes, atomic_write_json, mutation_lock


class SourceSyncError(RuntimeError):
    code = "project_source_sync_failed"


class SourceTransport(Protocol):
    def status(self, scope: dict) -> dict: ...
    def read(self, scope: dict, operation_id: str, revision: str | None = None) -> dict | None: ...
    def write(self, request: dict) -> dict: ...


_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_SHA = re.compile(r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
_SKIP = {'.git', '.venv', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
         'node_modules', '.runtime', '.tmp', '.adaos', 'ui_revisions'}
_EPHEMERAL = {'prompt_state.json', 'prep_result.json', 'builder.draft.json', '.DS_Store'}


def _token(value: str) -> str:
    if not _ID.fullmatch(value):
        raise SourceSyncError("Invalid source operation identity")
    return value


def capture_project(workspace: Path, project_id: str) -> SourceSnapshot:
    """Hold all participating Project files together, including tests/resources."""
    workspace = Path(workspace).resolve()
    project_id = _token(project_id)
    manifest = workspace / 'projects' / project_id / 'project.yaml'
    definition = yaml.safe_load(manifest.read_text(encoding='utf-8-sig'))
    if not isinstance(definition, dict) or definition.get('id') != project_id:
        raise SourceAdmissionError('Project folder and manifest identity disagree')
    assert_project_ownership(workspace / 'projects', definition)
    scopes = {('projects', project_id)}
    scopes.update((kind + 's', name) for ref in project_owned_refs(definition)
                  for kind, name in [ref.split(':')])
    for parts in scopes:
        root = workspace.joinpath(*parts)
        if not root.is_dir() or root.is_symlink() or getattr(root.lstat(), 'st_file_attributes', 0) & 0x400:
            raise SourceSyncError(f"Owned source is unavailable: {'/'.join(parts)}")

    def skip(relative: Path) -> bool:
        parts = relative.parts
        # The capture walker probes directories using a synthetic final filename.
        if relative.name == '.source-probe':
            directory = tuple(parts[:-1])
            selected = any(directory == scope[:len(directory)] or directory[:2] == scope for scope in scopes)
        else:
            selected = len(parts) >= 3 and tuple(parts[:2]) in scopes
        return (not selected or any(p in _SKIP for p in parts[2:-1])
                or relative.name in _EPHEMERAL or relative.suffix in {'.pyc', '.pyo'})

    snapshot = SourceSnapshot.capture(workspace, skip=skip)
    captured = yaml.safe_load(dict(snapshot.entries)[f'projects/{project_id}/project.yaml'])
    if captured != definition:
        raise SourceSyncError("Project composition changed during source capture")
    return snapshot


class ProjectSourceSync:
    def __init__(self, *, state_root: Path, workspace: Path, subnet_id: str, node_id: str,
                 transport: SourceTransport):
        self.root = Path(state_root) / 'checkpoint-intents' / 'projects'
        self.workspace = Path(workspace).resolve()
        self.subnet = _token(subnet_id)
        self.node = _token(node_id)
        self.transport = transport

    def _scope(self, project_id: str, change_id: str) -> dict:
        return {'project_id': _token(project_id), 'change_id': _token(change_id),
                'subnet_id': self.subnet, 'node_id': self.node}

    def _read_json(self, path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise SourceSyncError('Invalid retained source intent')
            return value
        except FileNotFoundError:
            return {}

    def _binding(self, scope: dict, remote: dict) -> dict:
        path = self.root / scope['project_id'] / 'binding.json'
        expected = {'project_id': scope['project_id'], 'subnet_id': self.subnet, 'node_id': self.node,
                    'workspace': str(self.workspace), 'repository': remote['repository'], 'target': remote['target']}
        current = self._read_json(path)
        if current:
            if any(current.get(k) != v for k, v in expected.items()):
                raise SourceSyncError('Project source repository/context binding changed')
            return current
        current = {**expected, 'context_generation': uuid4().hex}
        atomic_write_json(path, current)
        return current

    def _paths(self, scope: dict, operation_id: str) -> tuple[Path, Path]:
        folder = self.root / scope['project_id'] / 'operations' / _token(operation_id)
        return folder / 'intent.json', folder / 'source.zip'

    def _verify(self, intent: dict, response: dict) -> dict:
        for key, expected in intent['request'].items():
            if key != 'archive_b64' and response.get(key) != expected:
                raise SourceSyncError(f'Remote source marker differs: {key}')
        if response.get('state') != 'remote_confirmed' or not _SHA.fullmatch(str(response.get('commit') or '')):
            raise SourceSyncError('Remote source commit is unverified')
        request = intent['request']
        expected_path = f"subnets/{request['subnet_id']}/nodes/{request['node_id']}/projects/{request['project_id']}/source"
        if response.get('stored_path') != expected_path:
            raise SourceSyncError('Remote source path differs from Project binding')
        tree = str(response.get('tree') or '')
        if not _SHA.fullmatch(tree):
            raise SourceSyncError('Remote source tree is unverified')
        snapshot = SourceSnapshot.from_archive(base64.b64decode(response['archive_b64'], validate=True))
        if snapshot.digest != intent['snapshot_digest'] or snapshot.tree(tree) != tree or response.get('inventory') != snapshot.inventory:
            raise SourceSyncError('Remote full-Project source proof differs from retained snapshot')
        if intent.get('commit') and intent['commit'] != response['commit']:
            raise SourceSyncError('Source receipt commit changed')
        return {k: v for k, v in response.items() if k != 'archive_b64'}

    def _reconcile(self, intent: dict, intent_path: Path) -> dict:
        request = intent['request']
        scope = {k: request[k] for k in ('project_id', 'change_id', 'subnet_id', 'node_id')}
        response = self.transport.read(scope, request['operation_id'], intent.get('commit'))
        if response is None:
            if intent.get('state') == 'remote_confirmed':
                raise SourceSyncError('Previously confirmed remote source retention ref disappeared')
            return intent
        receipt = self._verify(intent, response)
        result = {**intent, 'state': 'remote_confirmed', 'commit': receipt['commit'], 'receipt': receipt}
        atomic_write_json(intent_path, result)
        confirmed_path = self.root / scope['project_id'] / 'confirmed.json'
        previous = self._read_json(confirmed_path)
        if int(intent['sequence']) >= int(previous.get('local_sequence', 0)):
            atomic_write_json(confirmed_path, {**receipt, 'local_sequence': intent['sequence']})
        return result

    def checkpoint(self, project_id: str, change_id: str, *, operation_id: str) -> dict:
        scope = self._scope(project_id, change_id)
        # This is a repository-wide gate, including unfinished writes after restart.
        with mutation_lock(self.root.parent / '.writer.lock', timeout_s=120):
            active_path = self.root / 'active.json'
            active = self._read_json(active_path)
            if active:
                previous_path, _ = self._paths(active['scope'], active['operation_id'])
                previous = self._read_json(previous_path)
                if not previous:
                    raise SourceSyncError('Active source operation lost its durable intent')
                previous = self._reconcile(previous, previous_path)
                same = active == {'scope': scope, 'operation_id': operation_id}
                if previous['state'] != 'remote_confirmed' and not same:
                    raise SourceSyncError('Previous source outcome requires reconciliation before another write')
                if previous['state'] == 'remote_confirmed':
                    atomic_write_json(active_path, {})
            intent_path, archive_path = self._paths(scope, operation_id)
            intent = self._read_json(intent_path)
            if intent:
                if any(intent['request'].get(k) != v for k, v in scope.items()):
                    raise SourceSyncError('Source operation identity was reused')
                frozen = SourceSnapshot.from_archive(archive_path.read_bytes())
                if frozen.digest != intent['snapshot_digest']:
                    raise SourceSyncError('Retained source snapshot changed')
                intent = self._reconcile(intent, intent_path)
            else:
                remote = self.transport.status(scope)
                with source_write_admission(self.workspace / 'projects'):
                    binding = self._binding(scope, remote)
                    frozen = capture_project(self.workspace, project_id)
                request = {**scope, 'operation_id': _token(operation_id), 'repository': binding['repository'],
                           'expected_base': remote['base'], 'expected_head': remote['head'],
                           'expected_writer': remote['writer'], 'snapshot_digest': frozen.digest,
                           'context_generation': binding['context_generation']}
                sequence_path = self.root / 'sequence.json'
                sequence = int(self._read_json(sequence_path).get('value', 0)) + 1
                atomic_write_json(sequence_path, {'value': sequence})
                intent = {'schema': 'adaos.development.source_intent.v1', 'state': 'prepared', 'sequence': sequence,
                          'request': request, 'snapshot_digest': frozen.digest, 'inventory': frozen.inventory}
                atomic_write_bytes(archive_path, frozen.archive())
                atomic_write_json(intent_path, intent)
            if intent['state'] != 'remote_confirmed':
                atomic_write_json(active_path, {'scope': scope, 'operation_id': operation_id})
                intent = {**intent, 'state': 'sending'}
                atomic_write_json(intent_path, intent)
                request = {**intent['request'], 'archive_b64': base64.b64encode(archive_path.read_bytes()).decode('ascii')}
                try:
                    # A response is a hint. Always perform an independent proof read.
                    self.transport.write(request)
                    intent = self._reconcile(intent, intent_path)
                    if intent['state'] != 'remote_confirmed':
                        raise SourceSyncError('Source delivery remains unverified')
                except Exception:
                    # Keep the original intent and snapshot; next observation/retry reads Git.
                    raise
            atomic_write_json(active_path, {})
            return self._projection(scope, intent)

    def _projection(self, scope: dict, intent: dict | None = None) -> dict:
        local_revision = None
        local_diagnostic = None
        try:
            with source_write_admission(self.workspace / 'projects'):
                local_revision = capture_project(self.workspace, scope['project_id']).digest
        except (SourceAdmissionError, SourceSnapshotError, SourceSyncError, OSError, UnicodeError, yaml.YAMLError) as exc:
            # A malformed/ambiguous current DEV must not hide a retained receipt
            # or remote outage. Capture/write admission remains strict.
            local_diagnostic = {'code': getattr(exc, 'code', 'local_source_unverified'), 'detail': str(exc)}
        confirmed = self._read_json(self.root / scope['project_id'] / 'confirmed.json')
        return {'project_id': scope['project_id'], 'local_revision': local_revision,
                'local_availability': 'verified' if local_revision else 'unverified',
                'local_diagnostic': local_diagnostic,
                'captured_revision': (intent or {}).get('snapshot_digest'),
                'confirmed_revision': confirmed.get('snapshot_digest'), 'confirmed': confirmed or None,
                'integration': confirmed.get('integration'),
                'state': ('local_unverified' if local_revision is None else
                          'synchronized' if confirmed.get('snapshot_digest') == local_revision else 'local_changes'),
                'operation_state': (intent or {}).get('state')}

    def status(self, project_id: str, change_id: str) -> dict:
        scope = self._scope(project_id, change_id)
        with mutation_lock(self.root.parent / '.writer.lock', timeout_s=120):
            active = self._read_json(self.root / 'active.json')
            availability = 'verified'
            observed = None
            divergent = False
            remote_diagnostic = None
            try:
                remote = self.transport.status(scope)
                binding = self._read_json(self.root / project_id / 'binding.json')
                if binding:
                    self._binding(scope, remote)
                if active:
                    path, _ = self._paths(active['scope'], active['operation_id'])
                    observed = self._reconcile(self._read_json(path), path)
                    if observed['state'] == 'remote_confirmed':
                        atomic_write_json(self.root / 'active.json', {})
                    else:
                        pending_remote = remote if active['scope'] == scope else self.transport.status(active['scope'])
                        expected = observed['request']
                        if any(pending_remote.get(key) != expected['expected_' + key] for key in ('base', 'head', 'writer')):
                            divergent = True
                confirmed = self._read_json(self.root / project_id / 'confirmed.json')
                if confirmed:
                    path, _ = self._paths(scope, confirmed['operation_id'])
                    # Receipt belongs to its original Change, not the requested current Change.
                    observed = self._reconcile(self._read_json(path), path)
                    if confirmed.get('change_id') == change_id and remote.get('head') != confirmed['commit']:
                        divergent = True
            except Exception as exc:
                availability = 'unverified'
                http_status = getattr(exc, 'status_code', None)
                error = ('Root source synchronization endpoint is unavailable (HTTP 404)'
                         if http_status == 404 else str(exc))
                remote_diagnostic = {
                    'code': ('source_endpoint_unavailable' if http_status == 404 else
                             getattr(exc, 'error_code', None) or 'remote_source_unverified'),
                    'http_status': http_status, 'detail': error,
                }
            result = self._projection(scope, observed)
            result['remote_availability'] = availability
            result['remote_diagnostic'] = remote_diagnostic
            if active:
                result['pending_operation'] = active
            if divergent:
                result['state'] = 'diverged'
            if availability != 'verified':
                result['state'] = 'reconciling'
                result['diagnostic'] = error
            elif not divergent and active and (observed or {}).get('state') != 'remote_confirmed':
                result['state'] = 'reconciling'
            return result

    def census(self) -> dict:
        """Inventory legacy and synthetic DEV equally; missing proof stays unknown."""
        projects_root = self.workspace / 'projects'
        with source_write_admission(projects_root):
            owners = ownership_inventory(projects_root)
            conflicts = {ref: values for ref, values in owners.items() if len(values) > 1}
            projects = []
            for manifest in sorted(projects_root.glob('*/project.yaml')):
                definition = yaml.safe_load(manifest.read_text(encoding='utf-8-sig'))
                token = _token(str(definition['id']))
                refs = project_owned_refs(definition)
                binding = self._read_json(self.root / token / 'binding.json')
                confirmed = self._read_json(self.root / token / 'confirmed.json')
                ambiguous = [ref for ref in refs if ref in conflicts]
                missing = [ref for ref in refs for kind, name in [ref.split(':')]
                           if not (self.workspace / (kind + 's') / name).is_dir()]
                projects.append({'project_id': token, 'version': str(definition.get('version') or ''),
                    'owned': list(refs), 'ownership_conflicts': ambiguous, 'missing_source': missing,
                    'repository': binding.get('repository'), 'context_generation': binding.get('context_generation'),
                    'confirmed_commit': confirmed.get('commit'), 'retention_ref': confirmed.get('retention_ref'),
                    'source_custody': 'confirmed_receipt_retained' if confirmed else 'unverified_legacy_source',
                    'admission': 'ownership_conflict' if ambiguous else 'missing_source' if missing else 'available'})
            unowned = []
            for kind in ('skill', 'scenario'):
                for manifest in sorted((self.workspace / (kind + 's')).glob(f'*/{kind}.yaml')):
                    ref = f'{kind}:{manifest.parent.name}'
                    if ref not in owners:
                        unowned.append(ref)
            return {'schema': 'adaos.development.source_census.v1', 'workspace': str(self.workspace),
                    'project_count': len(projects), 'projects': projects, 'conflicting_owners': conflicts,
                    'unowned_components': unowned, 'remote_proof': 'receipt_inventory_only_not_fresh_remote_verification'}

    def restore(self, project_id: str, change_id: str, *, operation_id: str, destination: Path) -> dict:
        """Restore only into a clean destination; applying to active DEV is separate."""
        scope = self._scope(project_id, change_id)
        intent_path, _ = self._paths(scope, operation_id)
        intent = self._read_json(intent_path)
        if not intent or intent['state'] != 'remote_confirmed':
            raise SourceSyncError('Restore requires a confirmed source operation')
        response = self.transport.read(scope, operation_id, intent['commit'])
        if not response:
            raise SourceSyncError('Confirmed remote source is unavailable')
        self._verify(intent, response)
        snapshot = SourceSnapshot.from_archive(base64.b64decode(response['archive_b64'], validate=True))
        snapshot.materialize(Path(destination))
        restored = capture_project(Path(destination), project_id)
        if restored.digest != snapshot.digest:
            raise SourceSyncError('Clean restore inventory differs')
        return {'state': 'restored', 'project_id': project_id, 'commit': intent['commit'],
                'snapshot_digest': restored.digest, 'inventory': restored.inventory}
