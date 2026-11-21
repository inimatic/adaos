"""Core's durable, sequential version reservations for independent source lists."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Iterable

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.release_source import validate_source_branch
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")


def _version(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(str(value))
    if not match:
        raise ValueError("Release counters require a plain three-part version")
    return tuple(map(int, match.groups()))


class ReleaseVersionAllocator:
    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / "release_versions"

    def reconcile_project_releases(self, *, project_id: str, source_branch: str, releases: Iterable) -> dict:
        """Adopt verified immutable legacy records, never a merged version string."""
        from adaos.domain.artifact_release import ProjectRelease
        branch = validate_source_branch(source_branch)
        component = 'project:' + project_id
        observed = []
        for release in releases:
            if not isinstance(release, ProjectRelease) or release.project_id != project_id or release.source_branch != branch:
                raise ValueError('Version reconciliation requires exact Project and source-list identities')
            sealed = release.seal()
            if release.release_digest != sealed.release_digest:
                raise ValueError('Version reconciliation requires a retained sealed release')
            _version(release.version)
            observed.append({'component':component, 'source_branch':branch, 'version':release.version,
                'release_digest':release.release_digest, 'source_revision':release.source_ref.revision,
                'package_digests':sorted(package.digest for package in release.components)})
        path = self.root / 'state.json'
        lane = canonical_payload_digest([component, branch])
        with mutation_lock(self.root / '.writer.lock', timeout_s=30):
            state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
                'schema':'adaos.release_versions.v1', 'lanes':{}, 'reservations':{}}
            if state.get('schema') != 'adaos.release_versions.v1':
                raise ValueError('Unsupported release version ledger')
            identities = state.setdefault('observed_releases', {})
            for item in observed:
                identity = canonical_payload_digest([component, branch, item['version']])
                if identity in identities and identities[identity] != item:
                    raise ValueError('A release-list version already has different immutable content')
                for reservation in state['reservations'].values():
                    basis = reservation['basis']
                    if (basis['component'] == component and basis['source_branch'] == branch
                            and reservation['version'] == item['version']
                            and reservation.get('release_digest') not in (None, item['release_digest'])):
                        raise ValueError('Observed release conflicts with the Core reservation')
                identities[identity] = item
            versions = [_version(item['version']) for item in observed]
            if lane in state['lanes']:
                versions.append(_version(state['lanes'][lane]['version']))
            if versions:
                version = '.'.join(map(str, max(versions)))
                state['lanes'][lane] = {'component':component, 'source_branch':branch, 'version':version}
                atomic_write_json(path, state)
            return {'component':component, 'source_branch':branch, 'observed':len(observed),
                    'version':state['lanes'].get(lane, {}).get('version')}

    def reserve(self, *, component: str, source_branch: str, operation_id: str,
                basis_digest: str, baseline: str, known_versions: Iterable[str] = (),
                major: int | None = None) -> dict:
        branch = validate_source_branch(source_branch)
        if not component or not operation_id or not basis_digest:
            raise ValueError("Version reservation requires component, operation and source basis")
        basis = {"component": component, "source_branch": branch, "operation_id": operation_id,
                 "basis_digest": basis_digest, "major": major}
        operation = canonical_payload_digest([component, branch, operation_id])
        lane = canonical_payload_digest([component, branch])
        path = self.root / "state.json"
        with mutation_lock(self.root / ".writer.lock", timeout_s=30):
            state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
                "schema": "adaos.release_versions.v1", "lanes": {}, "reservations": {}}
            if state.get("schema") != "adaos.release_versions.v1":
                raise ValueError("Unsupported release version ledger")
            previous = state["reservations"].get(operation)
            if previous:
                if previous["basis"] != basis:
                    raise ValueError("Version operation already names another source basis")
                return dict(previous)
            if lane in state["lanes"]:
                # The receiving list owns its counter after bootstrap. A source
                # merge can carry a higher (or lower) development manifest; it
                # must not rewrite the durable receiving-list counter.
                current = _version(state["lanes"][lane]["version"])
                if any(_version(value) > current for value in known_versions):
                    raise ValueError("Published versions exceed the Core counter; reconcile the receiving release list")
            else:
                current = max([_version(baseline), *[_version(v) for v in known_versions]])
            if major is not None:
                if type(major) is not int or major <= current[0]:
                    raise ValueError("Manual major must advance the selected release list")
                next_version = (major, 0, 0)
            else:
                next_version = (current[0], current[1], current[2] + 1)
            version = ".".join(map(str, next_version))
            reservation = {"basis": basis, "version": version, "status": "reserved"}
            state["reservations"][operation] = reservation
            state["lanes"][lane] = {"component": component, "source_branch": branch, "version": version}
            atomic_write_json(path, state)
            return dict(reservation)

    def seal(self, *, component: str, source_branch: str, operation_id: str,
             release_digest: str) -> dict:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", release_digest):
            raise ValueError("Exact release digest required")
        operation = canonical_payload_digest([component, validate_source_branch(source_branch), operation_id])
        path = self.root / "state.json"
        with mutation_lock(self.root / ".writer.lock", timeout_s=30):
            state = json.loads(path.read_text(encoding="utf-8"))
            reservation = state["reservations"][operation]
            identity = canonical_payload_digest([component, source_branch, reservation['version']])
            observed = state.get('observed_releases', {}).get(identity)
            if observed and observed['release_digest'] != release_digest:
                raise ValueError('Reserved version conflicts with its observed immutable release')
            if reservation.get("release_digest") not in {None, release_digest}:
                raise ValueError("Reserved release identity already has different immutable content")
            reservation.update(status="sealed", release_digest=release_digest)
            atomic_write_json(path, state)
            return dict(reservation)
