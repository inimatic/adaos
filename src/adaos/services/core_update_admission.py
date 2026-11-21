"""Fence production slot preparation and activation to one Hub policy revision."""
from __future__ import annotations

from typing import Any

from adaos.services import core_slots, source_policy
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.core_update_policy import current_env_type
from adaos.services.node_runtime_state import mutate_node_runtime_state


def governed(plan: dict[str, Any]) -> bool:
    return current_env_type() != 'dev' and str(plan.get('action') or 'update') == 'update'


def bind_update_source(plan: dict[str, Any], *, prepared: dict | None = None) -> dict | None:
    if not governed(plan):
        return None
    selected = source_policy.source_policy()
    expected = plan.get('source_policy')
    if expected is None and prepared is not None:
        expected = prepared.get('source_policy')
        if expected is None:
            raise source_policy.SourcePolicyError('prepared_source_policy_required')
    if expected is not None and expected != selected:
        raise source_policy.SourcePolicyError('source_policy_changed')
    source_policy.verify_source_release(str(plan.get('target_rev') or ''),
                                       str(plan.get('target_version') or ''), policy=selected)
    plan['source_policy'] = dict(selected)
    return selected


def verify_prepared_identity(plan: dict, manifest: dict) -> None:
    if ((manifest.get('source_branch') or manifest.get('target_rev')) != plan['target_rev']
            or manifest.get('git_commit') != plan['target_version']):
        raise source_policy.SourcePolicyError('prepared_release_identity_mismatch')


def retain_prepared_source(plan: dict, manifest: dict) -> dict:
    selected = bind_update_source(plan)
    if selected is None:
        return manifest
    if plan['target_slot'] == core_slots.active_slot():
        raise source_policy.SourcePolicyError('active_slot_cannot_be_prepared')
    verify_prepared_identity(plan, manifest)
    retained = {**manifest, 'source_policy': selected}
    atomic_write_json(core_slots.slot_manifest_path(plan['target_slot']), retained)
    return retained


def activate_prepared_source(plan: dict, activate) -> None:
    slot = plan['target_slot']
    if not governed(plan):
        activate(slot)
        return
    manifest = core_slots.read_slot_manifest(slot) or {}
    selected = bind_update_source(plan, prepared=manifest)

    def commit(state):
        # The policy writer and this final activation share the same lock.
        # Re-read the slot too: another preparation must not replace its bytes
        # between command completion and selection.
        if state.get('source_policy') != selected:
            raise source_policy.SourcePolicyError('source_policy_changed')
        if (str(getattr(source_policy._config(), 'role', '')).lower() == 'hub'
                and source_policy.configured_source_branch() != selected['branch']):
            raise source_policy.SourcePolicyError('source_policy_changed')
        current = core_slots.read_slot_manifest(slot) or {}
        verify_prepared_identity(plan, current)
        if current.get('source_policy') != selected:
            raise source_policy.SourcePolicyError('prepared_source_policy_changed')
        activate(slot)
        state['core_release_activation'] = {'state':'activated_unconfirmed', 'slot':slot,
            'policy':selected, 'branch':plan['target_rev'], 'commit':plan['target_version']}

    mutate_node_runtime_state(commit)


def activate_update_from_status(slot: str, status: dict, activate) -> None:
    """Supervisor cutover keeps the requested identity, not just a slot name."""
    plan = dict(status.get('plan') or {})
    for key in ('action', 'target_rev', 'target_version'):
        if status.get(key) is not None:
            plan[key] = status[key]
    plan['target_slot'] = slot
    activate_prepared_source(plan, activate)


def confirm_ready_source(slot: str, manifest: dict) -> bool:
    """Called after the Supervisor's readiness proof, never on command success."""
    from adaos.build_info import runtime_source_identity
    from adaos.services.runtime_identity import runtime_instance_id
    from datetime import datetime, timezone
    selected = source_policy.source_policy()
    owner = runtime_source_identity()
    branch = manifest.get('source_branch') or manifest.get('target_rev')
    commit = manifest.get('git_commit')
    if (manifest.get('source_policy') != selected or not commit
            or owner.get('commit') != commit or owner.get('branch') != branch):
        return False
    receipt = {'state':'confirmed', 'slot':slot, 'branch':branch, 'commit':commit,
        'version':owner.get('version'), 'policy':selected,
        'runtime_instance_id':runtime_instance_id(),
        'confirmed_at':datetime.now(timezone.utc).isoformat()}
    confirmed = False
    def record(state):
        nonlocal confirmed
        if state.get('source_policy') != selected:
            return
        activation = state.get('core_release_activation') or {}
        if activation.get('commit') != commit or activation.get('policy') != selected:
            return
        confirmed = True
        previous = state.get('confirmed_core_release') or {}
        if all(previous.get(key) == receipt[key] for key in ('slot','branch','commit','policy','runtime_instance_id')):
            return
        state['confirmed_core_release'] = receipt
    mutate_node_runtime_state(record)
    return confirmed
