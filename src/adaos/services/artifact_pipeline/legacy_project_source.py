"""Qualify legacy aggregate Candidate sources without changing the accepted release.

Older local Git adapters recorded a commit as the Project's source tree. Migration
requires an explicitly supplied repository, its exact historical commit and all
owned package bytes. It never reads mutable DEV or substitutes a current checkout.
"""
from __future__ import annotations

import io
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

import yaml

from adaos.domain.artifact_release import ArtifactSourceRef, canonical_payload_digest
from adaos.services.development_source_admission import project_owned_refs
from .cbs_authoring import compile_cbs_provider_files
from .packages import _build_excluded, _canonical_package_bytes
from .source_checkpoints import SOURCE_CHECKPOINT_SCHEMA, SourceCheckpointError
from .source_snapshots import MAX_BYTES, MAX_FILES, SourceSnapshot
from .storage import atomic_write_json, mutation_lock


LEGACY_PROJECT_CUSTODY = 'retained_legacy_project_source'


def is_legacy_project_source(candidate) -> bool:
    ref = candidate.verification_source_ref or candidate.source_ref
    return (ref == candidate.source_ref and ref.forge == 'github'
            and ref.path_scope == (f'projects/{candidate.project_id}/',)
            and re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', ref.revision) is not None
            and candidate.source_tree == ref.revision)


def _git_files(repository: Path, revision: str, scopes: list[str]) -> dict[str, bytes]:
    env = {**os.environ, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
    command = ['git', '-C', str(repository)]
    commit = subprocess.run([*command, 'rev-parse', '--verify', revision + '^{commit}'],
        env=env, capture_output=True, check=True, timeout=10).stdout.decode().strip()
    if commit != revision:
        raise SourceCheckpointError('Legacy source requires its exact immutable commit')
    listing = subprocess.run([*command, 'ls-tree', '-rzl', revision, '--', *scopes],
        env=env, capture_output=True, check=True, timeout=20).stdout
    entries = [entry.split(b'\t', 1) for entry in listing.split(b'\0') if entry]
    if len(entries) > MAX_FILES:
        raise SourceCheckpointError('Legacy source exceeds file limit')
    blobs = []
    total = 0
    for metadata, name in entries:
        mode, kind, digest, size = metadata.split()
        if mode != b'100644' or kind != b'blob':
            raise SourceCheckpointError('Legacy source requires regular files only')
        total += int(size)
        if total > MAX_BYTES:
            raise SourceCheckpointError('Legacy source exceeds byte limit')
        blobs.append((name.decode('utf-8'), digest, int(size)))
    result = subprocess.run([*command, 'cat-file', '--batch'],
        input=b''.join(digest + b'\n' for _, digest, _ in blobs),
        env=env, capture_output=True, check=True, timeout=30)
    stream = io.BytesIO(result.stdout)
    files = {}
    for name, digest, size in blobs:
        if stream.readline().split() != [digest, b'blob', str(size).encode()]:
            raise SourceCheckpointError('Legacy source Git object differs')
        data = stream.read(size)
        if len(data) != size or stream.read(1) != b'\n':
            raise SourceCheckpointError('Legacy source Git object is incomplete')
        files[name] = data
    return files


def qualify_legacy_project_source(publication, sync, candidate_id: str, *, repository: Path) -> dict:
    """Write a source-only custody receipt after proving every accepted package.

    An operator supplies the historical repository explicitly. Source custody
    uses the ordinary Core single writer and private Forge, including recovery.
    A receipt grants no new release decision or authority to alter application data.
    """
    candidate = publication.candidate_store.load(candidate_id)
    if candidate.status != 'accepted' or not is_legacy_project_source(candidate):
        raise SourceCheckpointError('Exact accepted legacy aggregate Candidate required')
    repository = Path(repository).expanduser().resolve(strict=True)
    if Path(candidate.source_ref.repository).resolve() != repository:
        raise SourceCheckpointError('Explicit repository differs from accepted source binding')
    plan = publication.get_candidate_release(candidate_id)
    checkpoint = publication.source_checkpoints
    path = checkpoint.path(candidate_id)
    with mutation_lock(publication.promotion_lock_path(candidate_id)):
        existing = checkpoint.receipt(candidate)
        if existing is not None:
            if existing.get('custody') != LEGACY_PROJECT_CUSTODY:
                raise SourceCheckpointError('Candidate already has another source custody receipt')
            return existing
        manifest_path = f'projects/{candidate.project_id}/project.yaml'
        manifest_files = _git_files(repository, candidate.source_ref.revision, [manifest_path])
        definition = yaml.safe_load(manifest_files.get(manifest_path, b''))
        if (not isinstance(definition, dict) or definition.get('id') != candidate.project_id
                or str(definition.get('version')) != candidate.version
                or set(project_owned_refs(definition)) != {p.key for p in plan.release.components}
                or plan.release.project_id != candidate.project_id
                or plan.release.version != candidate.version):
            raise SourceCheckpointError('Historical Project differs from the accepted release')
        scopes = [f'projects/{candidate.project_id}/'] + [f'{p.kind}s/{p.artifact_id}/' for p in plan.release.components]
        snapshot = SourceSnapshot(tuple(sorted(_git_files(repository, candidate.source_ref.revision, scopes).items())))
        files = dict(snapshot.entries)
        components = []
        for package in plan.release.components:
            scope = f'{package.kind}s/{package.artifact_id}/'
            expected_ref = ArtifactSourceRef(forge=candidate.source_ref.forge,
                repository=candidate.source_ref.repository, revision=candidate.source_ref.revision, path_scope=(scope,))
            if package.source_ref != expected_ref:
                raise SourceCheckpointError('Component source differs from the historical Project binding')
            source = {name[len(scope):]: raw for name, raw in files.items() if name.startswith(scope)}
            compilation = compile_cbs_provider_files(source, kind=package.kind, allow_generated_outputs=True)
            compiled = {**source, **(compilation.generated_files if compilation else {})}
            expected = {name: _canonical_package_bytes(raw) for name, raw in compiled.items()
                        if not _build_excluded(PurePosixPath(name))}
            data, verified = publication.package_store.read_verified(package.digest)
            if verified.ref != package:
                raise SourceCheckpointError('Accepted package reference differs')
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                actual = {name: archive.read(name) for name in verified.file_names}
            if expected != actual:
                raise SourceCheckpointError(f'Historical source does not produce the accepted package: {package.key}')
            components.append({'component_ref': package.key, 'package_digest': package.digest})
        operation_id = 'legacy-candidate-' + candidate.digest.removeprefix('sha256:')
        result = sync.checkpoint_frozen(candidate.project_id, operation_id,
            operation_id=operation_id, snapshot=snapshot)
        confirmed = result.get('confirmed') or {}
        if (result.get('operation_state') != 'remote_confirmed'
                or confirmed.get('snapshot_digest') != snapshot.digest
                or confirmed.get('tree') != snapshot.tree(confirmed.get('tree') or '0' * 40)):
            raise SourceCheckpointError('Legacy source custody is not independently confirmed')
        ref = ArtifactSourceRef(forge='adaos-root', repository=confirmed['repository'],
            revision=confirmed['commit'], path_scope=(confirmed['stored_path'].rstrip('/') + '/',))
        receipt = {'schema': SOURCE_CHECKPOINT_SCHEMA, **checkpoint.identity(candidate), 'status': 'completed',
            'custody': LEGACY_PROJECT_CUSTODY, 'source_ref': ref.to_dict(),
            'verified_source_tree': confirmed['tree'], 'snapshot_digest': snapshot.digest,
            'original_source_ref': candidate.source_ref.to_dict(), 'components': components,
            'source_operation_id': operation_id, 'source_retention_ref': confirmed['retention_ref']}
        receipt['receipt_digest'] = canonical_payload_digest(receipt)
        atomic_write_json(path, receipt)
        return receipt
