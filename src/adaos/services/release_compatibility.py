"""Read-only admission of a declared release composition and its data schemas.

Observations come from the node authority. A caller cannot replace missing
observations with a boolean approval, and executable rollback never restores data.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import re
from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.version import Version, InvalidVersion

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.release_source import validate_source_branch

SCHEMA = 'adaos.release.compatibility.v1'


class ReleaseCompatibilityError(ValueError):
    pass


def _mapping(value, code):
    if not isinstance(value, Mapping):
        raise ReleaseCompatibilityError(code)
    return value


def _names(value, code, *, empty=False):
    if (not isinstance(value, list) or len(value) > 512 or (not value and not empty)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise ReleaseCompatibilityError(code)
    return value


def verify_composition(contract: Mapping, observations: Mapping, *, policy_revision: int,
                       now: datetime | None = None) -> dict:
    if not isinstance(contract, Mapping) or contract.get('schema') != SCHEMA:
        raise ReleaseCompatibilityError('release_compatibility_contract_required')
    requirements = contract.get('requirements')
    if not isinstance(requirements, Mapping) or not {'client', 'backend'} <= requirements.keys():
        raise ReleaseCompatibilityError('release_composition_requirements_incomplete')
    if type(policy_revision) is not int or policy_revision < 1 or len(requirements) > 512:
        raise ReleaseCompatibilityError('release_policy_revision_invalid')
    _mapping(observations, 'release_composition_observation_required')
    installed = _mapping(observations.get('components'), 'release_composition_observation_required')
    for component, requirement in requirements.items():
        if not isinstance(component, str) or not component.strip():
            raise ReleaseCompatibilityError('release_component_requirement_invalid')
        actual = _mapping(installed.get(component), 'release_component_identity_unknown:' + component)
        if not isinstance(requirement, Mapping) or not requirement.get('version_spec'):
            raise ReleaseCompatibilityError('release_component_requirement_invalid:' + component)
        if (not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', str(actual.get('commit') or ''))
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(actual.get('package_digest') or ''))):
            raise ReleaseCompatibilityError('release_component_identity_unknown:' + component)
        try:
            supported = Version(actual['version']) in SpecifierSet(requirement['version_spec'])
        except (KeyError, InvalidVersion, InvalidSpecifier, TypeError):
            raise ReleaseCompatibilityError('release_component_version_unknown:' + component) from None
        branches = _names(requirement.get('source_branches'), 'release_component_branches_invalid:' + component)
        if not supported or actual.get('branch') not in branches:
            raise ReleaseCompatibilityError('release_component_incompatible:' + component)
        for branch in branches:
            validate_source_branch(branch)
    order = _names(contract.get('update_order'), 'release_update_order_required')
    if set(order) != set(requirements).union({'core'}):
        raise ReleaseCompatibilityError('release_update_order_required')
    # Admission is for the Core step. Earlier steps must already have exact,
    # confirmed installed identities; later steps must be compatible now too.
    for component in order[:order.index('core')]:
        if installed[component].get('state') != 'confirmed':
            raise ReleaseCompatibilityError('release_update_predecessor_unconfirmed:' + component)
    rolling = _mapping(contract.get('rolling'), 'release_rolling_bounds_required')
    lag, age = rolling.get('max_policy_revision_lag'), rolling.get('max_observation_age_seconds')
    if type(lag) is not int or lag < 0 or type(age) is not int or not 0 < age <= 86400:
        raise ReleaseCompatibilityError('release_rolling_bounds_required')
    clock = now or datetime.now(timezone.utc)
    def fresh(value):
        try:
            stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return stamp.tzinfo is not None and 0 <= (clock - stamp).total_seconds() <= age
        except (TypeError, ValueError):
            return False
    if not fresh(observations.get('observed_at')):
        raise ReleaseCompatibilityError('release_composition_observation_expired')
    members = observations.get('members')
    if not isinstance(members, list) or len(members) > 4096:
        raise ReleaseCompatibilityError('release_member_inventory_required')
    for member in members:
        _mapping(member, 'release_member_inventory_required')
        revision = member.get('policy_revision')
        if (type(revision) is not int or not 0 <= policy_revision - revision <= lag
                or not fresh(member.get('observed_at'))):
            raise ReleaseCompatibilityError('release_member_policy_skew_exceeded')
    data = contract.get('data')
    current_data = observations.get('data')
    if not isinstance(data, Mapping) or not isinstance(current_data, Mapping) or set(data) != set(current_data):
        raise ReleaseCompatibilityError('release_data_inventory_required')
    for store, requirement in data.items():
        if not isinstance(store, str) or not store.strip():
            raise ReleaseCompatibilityError('release_data_inventory_required')
        _mapping(requirement, 'release_data_requirement_invalid:' + store)
        current = _mapping(current_data[store], 'release_data_inventory_required')
        readable = _names(requirement.get('readable_schemas'), 'release_data_requirement_invalid:' + store)
        if (not current.get('schema') or current['schema'] not in readable
                or type(current.get('generation')) is not int or current['generation'] < 0):
            raise ReleaseCompatibilityError('release_data_schema_incompatible:' + store)
        writes = requirement.get('writes_schema')
        if not writes or writes not in readable:
            raise ReleaseCompatibilityError('release_data_write_schema_invalid:' + store)
        if writes != current['schema']:
            migration = _mapping(current.get('prepared_migration'), 'release_data_migration_recovery_required:' + store)
            if (migration.get('from_schema') != current['schema'] or migration.get('to_schema') != writes
                    or migration.get('generation') != current.get('generation')
                    or migration.get('status') != 'prepared'
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(migration.get('checkpoint_digest') or ''))
                    or not re.fullmatch(r'sha256:[0-9a-f]{64}', str(migration.get('restore_receipt_digest') or ''))):
                raise ReleaseCompatibilityError('release_data_migration_recovery_required:' + store)
    return {'schema':'adaos.release.compatibility_receipt.v1', 'status':'passed',
        'contract_digest':canonical_payload_digest(dict(contract)),
        'observations_digest':canonical_payload_digest(dict(observations)),
        'policy_revision':policy_revision, 'update_order':list(order),
        'data_rollback_performed':False}
