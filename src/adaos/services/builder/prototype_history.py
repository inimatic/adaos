"""Core-owned Prototype history, independent of disposable DEV activation.

Readers merge retained metadata, legacy revision filenames and workflow evidence.
They never materialize sources, repair state, or infer revisions from a release.
Only an explicit authoring/maintenance write retains a verified UI snapshot.
"""
from __future__ import annotations

import json
import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any, Mapping

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.resources.prototype import prototype_webui_digest

_REVISION = re.compile(r"^[0-9]{3,6}$")
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_CHANGE_LEDGERS = 512
_MAX_CHANGE_LEDGER_BYTES = 16 * 1024 * 1024
_MAX_CONTEXT_PACKETS = 1024
_MAX_CONTEXT_PACKET_BYTES = 4 * 1024 * 1024


class PrototypeHistoryService:
    def __init__(self, workflow):
        self.workflow = workflow

    def _scope(self, object_type: str, object_id: str) -> tuple[str, Path, Path]:
        if not _IDENTITY.fullmatch(object_id):
            raise ValueError('Invalid Prototype identity')
        if object_type == 'project':
            presentation = self.workflow._project_presentation(object_type, object_id) or {}
            scenario_id = str(presentation.get('scenario_id') or '')
        elif object_type == 'scenario':
            scenario_id = object_id
        else:
            raise ValueError('Prototype history requires a scenario or Project presentation')
        if not _IDENTITY.fullmatch(scenario_id):
            raise ValueError('Project has no Prototype presentation')
        try:
            source = self.workflow.project_root('scenario', scenario_id)
        except FileNotFoundError:
            source = Path(self.workflow.dev_scenarios_root) / scenario_id
        store = Path(self.workflow.state_dir) / 'builder' / 'prototype_history' / 'scenario' / scenario_id
        for candidate in (source, store):
            if candidate.is_symlink() or candidate.resolve() != candidate.absolute():
                raise ValueError('Prototype history must not traverse symlinks')
        return scenario_id, source, store

    def retain(self, object_type: str, object_id: str, revision: str) -> dict[str, Any]:
        """Retain an existing exact revision; never manufacture a missing one."""
        if not _REVISION.fullmatch(revision):
            raise ValueError('Invalid Prototype revision')
        scenario_id, source, store = self._scope(object_type, object_id)
        path = source / 'ui_revisions' / f'{revision}.json'
        if not path.is_file():
            return {'status': 'missing', 'revision': revision}
        if path.is_symlink() or path.resolve().parent != (source / 'ui_revisions').absolute():
            raise ValueError('Prototype revision must not traverse symlinks')
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Prototype revision exceeds retention size limit')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, Mapping) or not isinstance(value.get('after_webui'), Mapping):
            return {'status': 'invalid', 'revision': revision}
        if str(value.get('revision') or '') != revision or str(value.get('scenario_id') or '') != scenario_id:
            raise ValueError('Prototype snapshot identity mismatch')
        webui = dict(value['after_webui'])
        digest = prototype_webui_digest(webui)
        metadata = {'schema': 'adaos.builder.prototype_history_entry.v1', 'revision': revision,
                    'scenario_id': scenario_id, 'webui_digest': digest,
                    'created_at': value.get('created_at'),
                    'updated_at': value.get('created_at'), 'availability': 'retained'}
        return self._store(store, metadata, webui)

    @staticmethod
    def _store(store, metadata, webui):
        revision = metadata['revision']
        with mutation_lock(store / '.mutation.lock'):
            target = store / f'{revision}.json'
            if target.is_file():
                existing = json.loads(target.read_text(encoding='utf-8'))
                if existing.get('webui_digest') != metadata['webui_digest']:
                    raise ValueError('Prototype revision identity already has different content')
                return existing
            # No prompt files, model transcript, credentials, or mutable VCS receipt.
            atomic_write_json(store / 'snapshots' / f'{revision}.json', {
                **metadata, 'after_webui': webui})
            atomic_write_json(target, metadata)
        return metadata

    @staticmethod
    def _bounded_json_files(root: Path, *, maximum: int, max_bytes: int):
        """Yield regular JSON records from one exact Core-owned directory."""
        if not root.exists():
            return
        if root.is_symlink() or root.resolve() != root.absolute():
            raise ValueError('Prototype history evidence must not traverse symlinks')
        paths = sorted(root.glob('*.json'), key=lambda path: path.name)
        if len(paths) > maximum:
            raise ValueError('Prototype history evidence exceeds the bounded record count')
        for path in paths:
            if path.is_symlink() or path.resolve().parent != root.absolute():
                raise ValueError('Prototype history evidence must not traverse symlinks')
            if path.stat().st_size > max_bytes:
                raise ValueError('Prototype history evidence exceeds the bounded record size')
            try:
                value = json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                # A partially recovered obsolete ledger must not make the whole
                # lifecycle unreadable. It simply cannot prove a revision.
                continue
            if isinstance(value, Mapping):
                yield path, value

    def _revision_evidence(self, scenario_id: str) -> dict[str, dict[str, Any]]:
        """Read durable run receipts that outlive bounded workflow history.

        The receipt proves that a revision identity existed. It does not claim
        that the corresponding UI snapshot is still available.
        """
        root = (Path(self.workflow.state_dir) / 'builder' / 'change_runs'
                / 'scenario' / scenario_id)
        evidence: dict[str, dict[str, Any]] = {}
        for path, value in self._bounded_json_files(
                root, maximum=_MAX_CHANGE_LEDGERS,
                max_bytes=_MAX_CHANGE_LEDGER_BYTES) or ():
            change_id = str(value.get('change_id') or '').strip()
            if (value.get('schema') != 'adaos.builder.change_runs.v1' or not change_id
                    or hashlib.sha256(change_id.encode('utf-8')).hexdigest() != path.stem
                    or not isinstance(value.get('runs'), list)):
                continue
            for run in value['runs']:
                if (not isinstance(run, Mapping)
                        or run.get('activity') != 'prototype_revision_recorded'
                        or run.get('status') != 'succeeded'):
                    continue
                revisions = [str(ref).removeprefix('ui_revision:')
                             for ref in run.get('output_refs') or []
                             if str(ref).startswith('ui_revision:')]
                for revision in revisions:
                    if not _REVISION.fullmatch(revision):
                        continue
                    at = str(run.get('completed_at') or run.get('started_at') or '').strip() or None
                    row = evidence.setdefault(revision, {
                        'created_at': at, 'updated_at': at,
                        'evidence_count': 0, 'change_ids': [],
                    })
                    row['evidence_count'] += 1
                    if change_id not in row['change_ids']:
                        row['change_ids'].append(change_id)
                    if at and (not row.get('created_at') or at < row['created_at']):
                        row['created_at'] = at
                    if at and (not row.get('updated_at') or at > row['updated_at']):
                        row['updated_at'] = at
        return evidence

    def _accepted_evidence(self, scenario_id: str, acceptance_id: str) -> dict[str, Any]:
        """Resolve an acceptance only from a digest-addressed Core packet."""
        root = (Path(self.workflow.state_dir) / 'builder' / 'context_packets'
                / 'scenario' / scenario_id)
        matches: list[dict[str, Any]] = []
        for path, value in self._bounded_json_files(
                root, maximum=_MAX_CONTEXT_PACKETS,
                max_bytes=_MAX_CONTEXT_PACKET_BYTES) or ():
            supplied = str(value.get('digest') or '').strip().lower()
            unsigned = dict(value)
            unsigned.pop('digest', None)
            unsigned.pop('built_at', None)
            canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':')).encode('utf-8')
            expected = 'sha256:' + hashlib.sha256(canonical).hexdigest()
            if (value.get('schema') != 'adaos.builder.context_packet.v1'
                    or supplied != expected or supplied.removeprefix('sha256:') != path.stem):
                continue
            prototype = (value.get('artifacts') or {}).get('prototype') or {}
            acceptance = prototype.get('acceptance') or {}
            if (isinstance(acceptance, Mapping)
                    and acceptance.get('acceptance_id') == acceptance_id
                    and acceptance.get('decision') == 'accepted'):
                matches.append(dict(acceptance))
        identities = {(str(row.get('revision') or ''), str(row.get('webui_digest') or ''))
                      for row in matches}
        if len(identities) != 1:
            raise ValueError('Recovery requires one durable Prototype acceptance identity')
        return matches[0]

    def recover_evidenced_acceptance(
        self,
        object_type: str,
        object_id: str,
        source_snapshot: Mapping[str, Any],
        acceptance_id: str,
    ) -> dict[str, Any]:
        """Recover an historical accepted UI without changing DEV or head.

        Both the acceptance identity and the archived source are independently
        verified. This is an explicit maintenance write, never a read fallback.
        """
        scenario_id, _, store = self._scope(object_type, object_id)
        acceptance = self._accepted_evidence(scenario_id, str(acceptance_id or '').strip())
        revision = str(acceptance.get('revision') or '')
        digest = str(acceptance.get('webui_digest') or '')
        if not _REVISION.fullmatch(revision) or not digest.startswith('sha256:'):
            raise ValueError('Recovery acceptance identity is invalid')
        webui, manifest = self._snapshot_webui(source_snapshot, scenario_id)
        if prototype_webui_digest(webui) != digest:
            raise ValueError('Recovery snapshot does not match accepted Prototype digest')
        metadata = {
            'schema': 'adaos.builder.prototype_history_entry.v1',
            'revision': revision, 'scenario_id': scenario_id,
            'webui_digest': digest, 'created_at': acceptance.get('accepted_at'),
            'updated_at': acceptance.get('accepted_at'), 'availability': 'retained',
            'recovery': {'source_snapshot_digest': manifest['digest'],
                         'acceptance_id': acceptance['acceptance_id']},
        }
        return self._store(store, metadata, webui)

    def _snapshot_webui(self, source_snapshot: Mapping[str, Any], scenario_id: str):
        from adaos.services.skill_factory_sources import verify_source_snapshot
        manifest = verify_source_snapshot(state_dir=self.workflow.state_dir,
                                          reference=source_snapshot)
        artifacts = [item for item in manifest.get('artifacts') or []
                     if item.get('kind') == 'scenario' and item.get('id') == scenario_id]
        if len(artifacts) != 1:
            raise ValueError('Recovery snapshot does not bind the exact presentation scenario')
        root = Path(self.workflow.state_dir) / 'skill_factory/source_snapshots' / manifest['snapshot_id']
        relative = artifacts[0]['path'] + '/webui.json'
        if manifest.get('archive'):
            with zipfile.ZipFile(root / 'payload.zip') as archive:
                if archive.getinfo(relative).file_size > 32 * 1024 * 1024:
                    raise ValueError('Recovery snapshot exceeds read size limit')
                raw = archive.read(relative)
        else:
            raw = (root / relative).read_bytes()
        webui = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(webui, Mapping):
            raise ValueError('Recovery snapshot webui.json must be an object')
        return dict(webui), manifest

    def recover_accepted(self, object_type: str, object_id: str,
                         source_snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """Operator maintenance: recover only the already accepted exact UI.

        No user-supplied UI, arbitrary path, new acceptance, DEV restore, or
        workflow/current-pointer write is admitted by this recovery operation.
        """
        scenario_id, _, store = self._scope(object_type, object_id)
        state = self.workflow.lifecycle_state('scenario', scenario_id)
        acceptance = (state.get('prototype') or {}).get('acceptance') or {}
        revision = str(acceptance.get('revision') or '')
        if (acceptance.get('decision') != 'accepted' or not _REVISION.fullmatch(revision)
                or not acceptance.get('acceptance_id') or not acceptance.get('webui_digest')):
            raise ValueError('Recovery requires an existing exact Prototype acceptance')
        webui, manifest = self._snapshot_webui(source_snapshot, scenario_id)
        if prototype_webui_digest(webui) != acceptance['webui_digest']:
            raise ValueError('Recovery snapshot does not match accepted Prototype digest')
        metadata = {'schema': 'adaos.builder.prototype_history_entry.v1', 'revision': revision,
                    'scenario_id': scenario_id, 'webui_digest': acceptance['webui_digest'],
                    'created_at': acceptance.get('accepted_at'), 'updated_at': acceptance.get('accepted_at'),
                    'availability': 'retained', 'recovery': {
                        'source_snapshot_digest': manifest['digest'],
                        'acceptance_id': acceptance['acceptance_id']}}
        return self._store(store, metadata, webui)

    def _publication_lineage(self, scenario_id, state=None):
        """Resolve only ancestry anchored in the authoritative workflow head."""
        if state is None:
            state = self.workflow.lifecycle_state("scenario", scenario_id)
        head = state.get("prototype") or {}
        publication_ref = head.get("publication_ref")
        if publication_ref is None:
            return {}
        from .prototype_publications import PrototypePublications

        publications = PrototypePublications(self.workflow.state_dir)
        rows = publications.lineage(
            publication_ref, project_ref=f"scenario:{scenario_id}"
        )
        if (
            not rows
            or rows[0]["seed"]["revision"] != head.get("head_revision")
            or rows[0]["preparation_ref"] != head.get("preparation_ref")
        ):
            raise ValueError("Prototype publication history head mismatch")
        times = {
            event.get("metadata", {}).get("prototype_publication_ref"): event.get("at")
            for event in state.get("history") or []
            if isinstance(event, Mapping)
            and event.get("action") == "prototype_revision_recorded"
            and isinstance(event.get("metadata"), Mapping)
        }
        times.setdefault(publication_ref, head.get("revised_at"))
        return {
            row["seed"]["revision"]: {
                **row,
                "snapshot_available": publications.has_snapshot(row["publication_ref"]),
                "created_at": times.get(row["publication_ref"]),
            }
            for row in rows
        }

    def read(self, object_type: str, object_id: str, revision: str) -> dict[str, Any]:
        """Read an exact snapshot without restoring it into DEV or changing head."""
        if not _REVISION.fullmatch(revision):
            raise ValueError('Invalid Prototype revision')
        scenario_id, source, store = self._scope(object_type, object_id)
        publications = self._publication_lineage(scenario_id)
        publication = publications.get(revision)
        if publication is not None:
            from .prototype_publications import PrototypePublications
            value = PrototypePublications(self.workflow.state_dir).load(publication['publication_ref'])
            return {'schema': 'adaos.builder.ui_revision.v1', 'scenario_id': scenario_id,
                    'revision': revision, 'after_webui': value['compiled']['webui'],
                    'created_at': publication['created_at'],
                    'publication_ref': publication['publication_ref'],
                    'webui_digest': value['webui_digest'], 'availability': 'retained'}
        if publications and int(revision) > min(int(row['seed']['base_revision'] or 0) for row in publications.values()):
            raise FileNotFoundError('Prototype revision is not in the committed publication lineage')
        retained = store / 'snapshots' / f'{revision}.json'
        legacy = source / 'ui_revisions' / f'{revision}.json'
        path = retained if retained.is_file() else legacy
        expected_parent = store / 'snapshots' if path == retained else source / 'ui_revisions'
        if path.is_symlink() or path.resolve().parent != expected_parent.absolute():
            raise ValueError('Prototype snapshot must not traverse symlinks')
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Prototype revision exceeds read size limit')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if (not isinstance(value, Mapping) or value.get('revision') != revision
                or value.get('scenario_id') != scenario_id
                or not isinstance(value.get('after_webui'), Mapping)):
            raise ValueError('Prototype snapshot identity/content mismatch')
        if path == retained:
            metadata = self._metadata(store, scenario_id, revision)
            if not metadata or prototype_webui_digest(value['after_webui']) != metadata.get('webui_digest'):
                raise ValueError('Retained Prototype snapshot digest mismatch')
        return dict(value)

    @staticmethod
    def _metadata(store, scenario_id, revision):
        path = store / f'{revision}.json'
        if not path.is_file():
            return {}
        if path.is_symlink() or path.stat().st_size > 16 * 1024:
            raise ValueError('Invalid Prototype history metadata file')
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('revision') != revision
                or value.get('scenario_id') != scenario_id):
            raise ValueError('Prototype history metadata identity mismatch')
        return value

    def list(self, object_type: str, object_id: str, *, limit: int = 50,
             cursor: str | None = None) -> dict[str, Any]:
        if not 1 <= int(limit) <= 200:
            raise ValueError('Prototype history page size must be between 1 and 200')
        if cursor is not None and not _REVISION.fullmatch(cursor):
            raise ValueError('Invalid Prototype history cursor')
        if object_type == 'skill':
            if not _IDENTITY.fullmatch(object_id):
                raise ValueError('Invalid Prototype identity')
            return {'schema': 'adaos.builder.prototype_history.v1', 'applicable': False,
                    'items': [], 'current_revision': None, 'current': None, 'total': 0,
                    'has_more': False, 'next_cursor': None, 'missing_revisions': []}
        scenario_id, source, store = self._scope(object_type, object_id)
        try:
            # Prototype authority belongs to the presentation scenario, not the
            # Project catalog/release aggregate (which may have no UI workflow).
            state = self.workflow.lifecycle_state('scenario', scenario_id)
        except FileNotFoundError:
            state = {}  # Archived UI survives removal of disposable DEV sources.
        prototype = state.get('prototype') or {}
        publications = self._publication_lineage(scenario_id, state)
        current = str(prototype.get('head_revision') or '')
        legacy = source / 'ui_revisions'
        if legacy.is_symlink() or (store / 'snapshots').is_symlink():
            raise ValueError('Prototype history must not traverse symlinks')
        known = {p.stem for folder in (store, legacy) for p in folder.glob('*.json')
                 if _REVISION.fullmatch(p.stem) and not p.is_symlink()}
        durable_evidence = self._revision_evidence(scenario_id)
        known.update(durable_evidence)
        known.update(publications)
        # Workflow records prove identity, not that a snapshot is still available.
        for event in state.get('history') or []:
            if isinstance(event, Mapping) and event.get('action') == 'prototype_revision_recorded':
                token = str((event.get('metadata') or {}).get('revision') or '')
                if _REVISION.fullmatch(token):
                    known.add(token)
        if _REVISION.fullmatch(current):
            known.add(current)
        if publications:
            legacy_boundary = min(int(row['seed']['base_revision'] or 0) for row in publications.values())
            known = {revision for revision in known if revision in publications or int(revision) <= legacy_boundary}
        ordered = sorted(known, key=lambda token: (int(token), token), reverse=True)
        remaining = [r for r in ordered if cursor is None or (int(r), r) < (int(cursor), cursor)]

        def item(revision):
            local = legacy / f'{revision}.json'
            publication = publications.get(revision)
            if publication is not None:
                available = publication['snapshot_available']
                return {'revision': revision, 'scenario_id': scenario_id,
                        'publication_ref': publication['publication_ref'],
                        'webui_digest': publication['webui_digest'],
                        'authority': 'committed_publication',
                        'created_at': publication['created_at'], 'updated_at': publication['created_at'],
                        'availability': 'retained' if available else 'missing',
                        'snapshot_available': available, 'source_available': local.is_file(),
                        'current': revision == current}
            metadata = self._metadata(store, scenario_id, revision)
            receipt = durable_evidence.get(revision) or {}
            retained = store / 'snapshots' / f'{revision}.json'
            if retained.is_symlink():
                raise ValueError('Prototype snapshot must not traverse symlinks')
            availability = 'retained' if metadata and retained.is_file() else 'legacy' if local.is_file() and not local.is_symlink() else 'missing'
            return {**receipt, **metadata, 'revision': revision, 'scenario_id': scenario_id,
                    'availability': availability, 'snapshot_available': availability != 'missing',
                    'source_available': local.is_file() and not local.is_symlink(),
                    'current': revision == current,
                    'updated_at': metadata.get('updated_at') or receipt.get('updated_at')}

        page = remaining[:int(limit)]
        return {'schema': 'adaos.builder.prototype_history.v1', 'items': [item(r) for r in page],
                'current_revision': current or None, 'current': item(current) if current in known else None,
                'total': len(ordered), 'has_more': len(remaining) > len(page),
                'next_cursor': page[-1] if len(remaining) > len(page) else None,
                'missing_revisions': [r for r in ordered if
                    (not publications[r]['snapshot_available'] if r in publications else
                     not (legacy / f'{r}.json').is_file() and not (store / 'snapshots' / f'{r}.json').is_file())]}
