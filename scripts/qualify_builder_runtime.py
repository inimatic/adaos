"""Prepare a verified Builder package, then qualify activation and exact rollback.

DEV-only, model-free. Restart is a separately observed gate, never inferred from
activation. The old slot is preserved and the candidate is left active only
after the rollback/reactivation sequence passes.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from dotenv import load_dotenv
load_dotenv()
from adaos.apps.cli.app import Settings, init_ctx
from adaos.apps.cli.active_control import resolve_control_token
from adaos.services.agent_context import get_ctx
from adaos.services.skill.manager import SkillManager
from adaos.services.builder.delivery_qualification import qualify_message_hook, qualify_native_proposal_boundary
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore, build_artifact_package, artifact_source_snapshot
from adaos.domain.artifact_release import ArtifactPackageRef
from adaos.services.artifact_pipeline.storage import atomic_write_json

parser = argparse.ArgumentParser(__doc__)
parser.add_argument('--delivery', type=Path, required=True)
parser.add_argument('--run', action='store_true')
args = parser.parse_args()
if os.getenv('ENV_TYPE') != 'dev' or not args.run:
    parser.error('Requires ENV_TYPE=dev and --run')
delivery = args.delivery.resolve()
if not delivery.is_relative_to(ROOT / 'e2e/artifacts'):
    parser.error('Delivery must be retained under e2e/artifacts')
manifest = json.loads((delivery / 'delivery-manifest.json').read_text(encoding='utf-8'))
package = ArtifactPackageRef.from_mapping(manifest['builder']['package'])
if package.artifact_id != 'builder_skill' or not manifest['builder']['materialized_native']['status'] == 'passed':
    raise ValueError('Candidate must pass the native package gate')
source = delivery / 'materialized-builder'
rebuilt = build_artifact_package(source, kind='skill', source_ref=package.source_ref)
if rebuilt.ref.digest != package.digest:
    raise ValueError('Materialized source differs from the qualified package')
init_ctx(Settings.from_sources())
ctx = get_ctx()
manager = SkillManager(git=ctx.git, paths=ctx.paths, caps=ctx.caps, settings=ctx.settings, bus=ctx.bus)
previous = manager.active_runtime_package_identity('builder_skill')
environment = manager._runtime_env('builder_skill')
previous_source = environment.build_slot_paths(previous['version'], previous['slot']).src_dir / 'skills/builder_skill'
previous_source_digest = artifact_source_snapshot(previous_source)['digest']
candidate_slot = environment.select_inactive_slot(package.version)
slot_path = environment.build_slot_paths(package.version, candidate_slot).root.resolve()
runtime_root = (Path(ctx.paths.skills_dir()) / '.runtime/builder_skill').resolve()
if not slot_path.is_relative_to(runtime_root):
    raise ValueError('Candidate slot escapes Builder runtime')
backup = ROOT / '.adaos/recovery/private-runtime' / ('builder-delivery-' + delivery.name)
backup.mkdir(parents=True, exist_ok=False)
if slot_path.exists():
    shutil.copytree(slot_path, backup / ('previous-slot-' + candidate_slot))
atomic_write_json(backup / 'previous-selection.json', previous)
store = ContentAddressedPackageStore(Path(ctx.paths.state_dir()) / 'artifact_pipeline/packages')
archive = ContentAddressedPackageStore(delivery / 'package-store').package_path(package.digest).read_bytes()
store.put(archive, expected_digest=package.digest)
prepared = manager.prepare_runtime('builder_skill', path=source, preferred_slot=candidate_slot,
    package_digest=package.digest, package_manifest_digest=package.manifest_digest,
    source_manifest_digest=manifest['builder']['source_snapshot_digest'], run_tests=False)
session = requests.Session()
session.trust_env = False
session.headers['Authorization'] = 'Bearer ' + resolve_control_token(base_url='http://127.0.0.1:8777')
rows = []
def activate(version, slot, gate):
    pins = ({'package_digest': package.digest, 'package_manifest_digest': package.manifest_digest,
             'source_manifest_digest': manifest['builder']['source_snapshot_digest']} if version == package.version else {})
    response = session.post('http://127.0.0.1:8777/api/skills/runtime/activate', json={
        'name': 'builder_skill', 'version': version, 'slot': slot, 'auto_prepare': False,
        'webspace_id': 'test_ctxqual_be8422f1050250', **pins}, timeout=180)
    response.raise_for_status()
    observed = manager.active_runtime_package_identity('builder_skill')
    if observed['version'] != version or observed['slot'] != slot:
        raise ValueError('Activation did not converge to the exact selection')
    if version != package.version:
        if artifact_source_snapshot(previous_source)['digest'] != previous_source_digest:
            raise ValueError('Rollback changed the previous source bytes')
    rows.append({'gate': gate, 'identity': observed, 'ok': True})
    atomic_write_json(delivery / 'runtime-progress.json', {'rows': rows, 'complete': False})
    print('runtime-gate=' + gate + ' ok=true', flush=True)
try:
    activate(package.version, prepared.slot, 'activate-candidate')
    manager.adopt_active_runtime_package_identity('builder_skill', package=package.to_dict())
    active_root = environment.build_slot_paths(package.version, prepared.slot).src_dir / 'skills/builder_skill'
    active_source = (active_root / 'handlers/main.py').read_text(encoding='utf-8')
    boundary = qualify_native_proposal_boundary(active_source, scratch_root=ROOT / '.tmp/runtime-native-boundary')
    if boundary != manifest['builder']['materialized_native']:
        raise ValueError('Active native entry differs from materialized package')
    activate(previous['version'], previous['slot'], 'rollback-exact-previous')
    activate(package.version, prepared.slot, 'reactivate-candidate')
except BaseException:
    activate(previous['version'], previous['slot'], 'failure-restores-previous')
    raise
result = {'schema': 'adaos.builder.runtime_delivery_qualification.v1', 'ok': True,
    'package': package.to_dict(), 'previous': previous, 'rows': rows,
    'active': manager.active_runtime_package_identity('builder_skill'), 'native': boundary,
    'restart_qualified': False, 'model_calls': 0, 'usd': 0}
atomic_write_json(delivery / 'runtime-result.json', result)
print(json.dumps({'ok': True, 'active': result['active'], 'restart_qualified': False}), flush=True)
