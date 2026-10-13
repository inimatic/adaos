"""Reproducible, model-free qualification of an explicitly selected Builder source.

Package materialization is not activation. Workspace, package and active runtime
are separate consumers and never acquire each other's qualification implicitly.
"""
from __future__ import annotations

import ast
import io
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from adaos.domain.artifact_release import ArtifactSourceRef, canonical_payload_digest
from adaos.services.artifact_pipeline.packages import (
    ContentAddressedPackageStore, artifact_source_snapshot, build_artifact_package,
)
from adaos.services.builder.semantic_admission import compiler_digest
from adaos.services.ui_component_contracts import component_registration_types, project_component_contract


class DeliveryQualificationError(ValueError):
    pass


def qualify_message_hook(source: str) -> dict[str, Any]:
    """Execute only the assembly boundary, with no providers or module side effects."""
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_builder_llm_messages']
    if len(functions) != 1:
        raise DeliveryQualificationError('delivery.builder_hook_missing')
    function = functions[0]
    checks = []
    for strategy in (None, 'full', 'hybrid-focused'):
        calls = []
        def compiler(messages):
            calls.append(messages)
            return messages, {'strategy': strategy}
        namespace = {'Mapping': Mapping, 'Any': Any, '_compact_json': json.dumps,
            'os': SimpleNamespace(getenv=lambda key, default=None: strategy if key == 'ADAOS_BUILDER_PROTOTYPE_CONTEXT_STRATEGY' and strategy is not None else default),
            'sdk_builder_prototype': SimpleNamespace(compile_generation_messages=compiler)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<declared-builder-hook>', 'exec'), namespace)
        request = {'system_prompt': 'policy', 'stable_user_prompt': 'stable'}
        messages, purposes = namespace['_builder_llm_messages'](request, 'task')
        if [item['content'] for item in messages] != ['policy', 'stable', 'task'] or len(purposes) != len(messages):
            raise DeliveryQualificationError('delivery.builder_hook_changes_full_boundary')
        if len(calls) != (1 if strategy == 'hybrid-focused' else 0):
            raise DeliveryQualificationError('delivery.builder_hook_opt_in_missing')
        if strategy == 'hybrid-focused':
            namespace['sdk_builder_prototype'] = SimpleNamespace()
            try:
                namespace['_builder_llm_messages'](request, 'task')
            except RuntimeError:
                pass
            else:
                raise DeliveryQualificationError('delivery.builder_hook_missing_sdk_not_rejected')
        checks.append(strategy or 'default-full')
    return {'status': 'passed', 'checks': checks, 'hook_digest': canonical_payload_digest(ast.dump(function)), 'model_calls': 0}


def qualify_delivery(*, repo_root: Path, builder_source: Path, package_store: Path,
                     materialization_target: Path, active_runtime: Path | None = None,
                     profile: str = 'semantic-authoring') -> dict[str, Any]:
    """Build twice, verify/install bytes through the common store, retain one manifest."""
    if profile not in ('semantic-authoring', 'message-boundary'):
        raise DeliveryQualificationError('delivery.unknown_profile')
    source = builder_source.resolve()
    target = materialization_target.resolve()
    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):
        raise DeliveryQualificationError('delivery.materialization_target_not_fresh_or_overlaps_source')
    source_text = (source / 'handlers/main.py').read_text(encoding='utf-8')
    names = {node.name for node in ast.parse(source_text).body if isinstance(node, ast.FunctionDef)}
    required = {'_builder_llm_messages', '_label_conversation_target_guard', '_try_bounded_semantic_request', '_try_semantic_label_request'}
    if profile == 'semantic-authoring' and not required <= names:
        raise DeliveryQualificationError('delivery.semantic_authoring_source_incomplete:' + ','.join(sorted(required - names)))
    source_before = artifact_source_snapshot(builder_source)
    source_ref = ArtifactSourceRef(forge='local', repository='declared-builder-source',
        revision=source_before['digest'], path_scope=('skills/builder_skill/',))
    source_hook = qualify_message_hook(source_text)
    first = build_artifact_package(builder_source, kind='skill', source_ref=source_ref)
    second = build_artifact_package(builder_source, kind='skill', source_ref=source_ref)
    if first.ref.digest != second.ref.digest or first.archive_bytes != second.archive_bytes:
        raise DeliveryQualificationError('delivery.package_nondeterministic')
    with zipfile.ZipFile(io.BytesIO(first.archive_bytes)) as archive:
        packaged_hook = qualify_message_hook(archive.read('handlers/main.py').decode('utf-8'))
    if packaged_hook['hook_digest'] != source_hook['hook_digest']:
        raise DeliveryQualificationError('delivery.package_hook_drift')
    store = ContentAddressedPackageStore(package_store)
    store.put(first.archive_bytes, expected_digest=first.ref.digest)
    store.materialize(first.ref.digest, materialization_target)
    installed_hook = qualify_message_hook((materialization_target / 'handlers/main.py').read_text(encoding='utf-8'))
    if installed_hook['hook_digest'] != packaged_hook['hook_digest']:
        raise DeliveryQualificationError('delivery.materialized_hook_drift')
    source_after = artifact_source_snapshot(builder_source)
    if source_before['digest'] != source_after['digest']:
        raise DeliveryQualificationError('delivery.source_changed_during_build')
    client = repo_root / 'src/adaos/integrations/adaos-client'
    def git(path, *args):
        return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()
    client_revision = git(client, 'rev-parse', 'HEAD')
    gitlink = git(repo_root, 'ls-tree', 'HEAD', 'src/adaos/integrations/adaos-client').split()[2]
    sha_pin = client.with_suffix('.sha').read_text(encoding='utf-8').strip()
    if client_revision != gitlink or gitlink != sha_pin:
        raise DeliveryQualificationError('delivery.client_pin_mismatch')
    generated = (client / 'src/app/runtime/component-contract.generated.ts').read_text(encoding='utf-8')
    if generated != component_registration_types():
        raise DeliveryQualificationError('delivery.client_projection_stale')
    inventory = subprocess.run(['node', str(client / 'scripts/generate-client-capability-inventory.mjs'), '--check'],
        cwd=client, capture_output=True, text=True)
    if inventory.returncode:
        raise DeliveryQualificationError('delivery.client_inventory_stale')
    snapshot = project_component_contract()
    from adaos.services.ui_capabilities import ui_capability_catalog
    from adaos.services.skill_factory_worker import _implementation_sdk_contract_bundle
    from adaos.services.root_mcp.descriptor_search import get_descriptor_item
    for unit in snapshot['retrieval_units']:
        if get_descriptor_item(**unit['locator'])['item']['component_contract'] != unit:
            raise DeliveryQualificationError('delivery.mcp_projection_drift')
    active = qualify_message_hook((active_runtime / 'handlers/main.py').read_text(encoding='utf-8')) if active_runtime else None
    manifest = {'schema': 'adaos.builder.delivery_manifest.v1',
        'core_revision': git(repo_root, 'rev-parse', 'HEAD'), 'compiler_digest': compiler_digest(),
        'component_contract': snapshot['provenance'], 'sdk_digest': canonical_payload_digest(_implementation_sdk_contract_bundle()),
        'catalog_digest': ui_capability_catalog()['catalog_digest'],
        'client': {'revision': client_revision, 'gitlink': gitlink, 'sha_pin': sha_pin,
            'registration_digest': canonical_payload_digest(generated),
            'inventory_digest': canonical_payload_digest(json.loads((client / 'architecture/evidence/client-capability-inventory.v1.json').read_text(encoding='utf-8')))},
        'builder': {'source_snapshot_digest': source_before['digest'], 'package': first.ref.to_dict(),
            'source_hook': source_hook, 'package_hook': packaged_hook, 'materialized_hook': installed_hook,
            'active_hook': active, 'active_runtime_is_same_package': None, 'declared_profile': profile,
            'declared_source_has_authoring_surface': required <= names},
        'consumer_masks': {unit['id']: {'content_digest': unit['content_digest'], 'locator': unit['locator']} for unit in snapshot['retrieval_units']},
        'qualification_scope': 'declared-source/package/materialized hook and common contract projections; not active-package identity or full CI',
        'cost': {'model_calls': 0, 'tokens': 0, 'usd': 0}}
    manifest['manifest_digest'] = canonical_payload_digest(manifest)
    return manifest
