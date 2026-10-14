"""Core-owned discovery input for ordinary Builder automation assignments.

Requirement intent cannot grant authority or assert provider availability.
Without an installed environment observation we use an empty-authority local
profile. Missing registry facts are typed gaps, never legacy reuse permission.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.capability_binding_state import ApplicationRequirement, EnvironmentProfile
from adaos.services.applications.cbs import ApplicationCBSService
from adaos.services.builder.cbs_intent import validate_cbs_intent
from adaos.services.builder.prototype_stage import prototype_cbs_intent
from adaos.services.capability_binding_state.registry_projection import SemanticRegistryProjection


def discovery_from_assignment_snapshot(*, task: Mapping[str, Any], state_dir: Path,
                                      registry_root: Path) -> dict[str, Any] | None:
    """Produce the query during assignment creation from verified archive bytes."""
    import json
    import zipfile
    from adaos.services.skill_factory_sources import verify_source_snapshot
    target = task.get('target') or {}
    reference = (task.get('forge') or {}).get('source_snapshot')
    if target.get('type') != 'scenario' or not isinstance(reference, Mapping):
        return None
    import re
    token = str(reference.get('snapshot_id') or '')
    if not re.fullmatch('[a-f0-9]{64}', token):
        raise ValueError('authoring.assignment_source_identity_invalid')
    if not (state_dir/'skill_factory/source_snapshots'/token/'snapshot.json').is_file():
        # A distributed dispatcher may hold only the immutable source ref. Its
        # worker produces the query after resolving and verifying that source.
        return {'status': 'insufficient', 'findings': [{'code': 'authoring.assignment_source_unavailable'}], 'callable_tools': []}
    snapshot = verify_source_snapshot(state_dir=state_dir, reference=reference)
    member = f"scenarios/{target['id']}/webui.json"
    archive = state_dir/'skill_factory/source_snapshots'/snapshot['snapshot_id']/'payload.zip'
    if not archive.is_file():
        # Legacy directory snapshots still pass their exact tree verification.
        file = archive.parent/member
        if not file.is_file(): return None
        webui = json.loads(file.read_text(encoding='utf-8'))
    else:
        with zipfile.ZipFile(archive) as opened:
            if member not in opened.namelist(): return None
            webui = json.loads(opened.read(member))
    return prepare_assignment_discovery(webui=webui, state_dir=state_dir, registry_root=registry_root,
        application_ref='scenario:'+target['id'], task_id=task['task_id'])


def prepare_assignment_discovery(*, webui: Mapping[str, Any], state_dir: Path,
                                registry_root: Path, application_ref: str,
                                task_id: str) -> dict[str, Any] | None:
    raw = prototype_cbs_intent(webui)
    if raw is None:
        return None
    intent = validate_cbs_intent(raw)
    try:
        projection = SemanticRegistryProjection(registry_root, state_dir)
        index = projection.read_index()
        revision = subprocess.check_output(['git', '-C', str(registry_root), 'rev-parse', 'HEAD'],
            text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        return {'status': 'insufficient', 'findings': [{'code': 'authoring.registry_snapshot_unavailable',
            'error_type': type(exc).__name__}], 'callable_tools': []}
    admission = ApplicationCBSService(state_dir).inspect_admission(application_ref)
    # This observation is owned by the installation service, not task artifacts.
    profile = EnvironmentProfile.from_mapping(admission['environment_profile']) if admission else EnvironmentProfile.create(
        profile_ref='profile:local/default', profile_class='local', modes=('production',),
        provider_features=(), authorities=(), guarantees={'consistency': [], 'durability': [], 'isolation': []})
    requirements = [ApplicationRequirement.create(
        requirement_ref=f"requirement:{application_ref.replace(':', '.')}.{item['id']}",
        capability_ref=item['capability_ref'], contract_range=item['contract_range'],
        environment_target={'profile_ref': profile.profile_ref, 'allowed_modes': ['production']},
        policy_constraints={'locality': item.get('locality', 'remote_allowed'),
            'privacy': item.get('privacy', 'application-declared'),
            'required_authorities': list(item.get('required_authorities', []))},
        evidence_threshold={'required_claim_kinds': list(item.get('required_claim_kinds', ['capability_conformance'])),
            'allow_stale': False}).to_dict() for item in intent['requirements']]
    query = {'schema': 'adaos.semantic_registry.query.v1', 'query_ref': 'registry-query:assignment/' + task_id,
        'snapshot': {'registry_revision': revision, 'index_digest': index['index_digest']},
        'requirements': requirements, 'environment_profile': profile.to_dict(), 'target_mode': 'production',
        'policy_inputs': {'allowed_publisher_refs': [], 'denied_publisher_refs': [], 'required_authorities': []},
        'evidence_inputs': {'as_of': datetime.now(timezone.utc).isoformat(), 'accepted_results': ['verified'],
            'required_claim_kinds': sorted({kind for item in requirements for kind in item['evidence_threshold']['required_claim_kinds']}),
            # Portable claims are not live dependency observations. Unknown
            # external state remains insufficient until its owner supplies it.
            'dependency_observations': []}}
    return {'status': 'prepared', 'query': query, 'findings': [],
        'profile_source': 'installed_admission' if admission else 'unobserved_empty_authority',
        'callable_tools': []}
