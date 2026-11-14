"""Qualify the selected Management Beta without migrating data or accepting Stable."""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import uuid

from dotenv import load_dotenv
from adaos.apps.cli.app import Settings, init_ctx
from adaos.apps.cli.active_control import resolve_control_base_url, resolve_control_token
from adaos.services.agent_context import get_ctx
from adaos.services.applications.runtime import get_application_service
from adaos.services.node_maintenance.validation import record_run
from adaos.services.storage_hygiene import managed_storage_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--desired-path', required=True)
    parser.add_argument('--save-intent', action='store_true', help='Save desired placement only; existing paths and data remain active.')
    parser.add_argument('--client', default='http://127.0.0.1:8100/')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / '.env')
    if os.environ.get('ENV_TYPE') != 'dev':
        parser.error('Owned local DEV qualification is required')
    output = args.output.resolve()
    if not output.is_relative_to(root / 'e2e/artifacts') or output.exists():
        parser.error('Use a new standard E2E artifact directory')
    init_ctx(Settings.from_sources())
    ctx = get_ctx()
    store = get_application_service(ctx.paths.state_dir()).store
    selected = [row for row in store.list_runtime_selections()
                if row.application_id == 'web_desktop' and row.webspace_id == 'desktop']
    if len(selected) != 1 or selected[0].to_dict().get('source') != 'local_trial':
        parser.error('Select the owned Management Beta before qualification')
    selection = selected[0].to_dict()
    script = root / 'e2e/stand/browser/node-maintenance.mjs'
    manifest = {'schema':'adaos.e2e.node_maintenance.v1', 'run_id':uuid.uuid4().hex,
        'started_at':datetime.now(timezone.utc).isoformat(),
        'suite':{'suite_id':'node-maintenance-beta', 'profile':'requirement_delta'},
        'selection':selection, 'desired_path':args.desired_path,
        'saves_intent':args.save_intent, 'moves_data':False,
        'test_digest':'sha256:' + hashlib.sha256(script.read_bytes()).hexdigest(),
        'runner_digest':'sha256:' + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()}
    env = {**os.environ, 'ADAOS_E2E_HUB_URL':resolve_control_base_url(prefer_local=True),
        'ADAOS_E2E_HUB_TOKEN':resolve_control_token(), 'ADAOS_E2E_SUBNET_ID':ctx.config.subnet_id,
        'ADAOS_E2E_RELEASE_DIGEST':selection['release_digest'], 'ADAOS_E2E_OUTPUT':str(output),
        'ADAOS_E2E_CLIENT_URL':args.client, 'ADAOS_E2E_DESIRED_PATH':args.desired_path,
        'ADAOS_E2E_SAVE_PLACEMENT_INTENT':'1' if args.save_intent else '0'}
    return _execute(root, output, manifest, env, ctx.paths.state_dir())


def _execute(root, output, manifest, env, state_dir):
    script = root / 'e2e/stand/browser/node-maintenance.mjs'
    status = 'inconclusive'
    try:
        with managed_storage_run(output,owner='e2e.node-maintenance',retention_class='e2e_run',
                success_retain_seconds=7*86400,failure_retain_seconds=7*86400,
                metadata={'suite_id':'node-maintenance-beta','run_id':manifest['run_id']}):
            (output / 'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
            record_run(state_dir,output,kind='stand',manifest=manifest,status='running')
            result = subprocess.run(['node',str(script)],cwd=root,env=env,capture_output=True,timeout=420)
            (output / 'runner.stdout.log').write_bytes(result.stdout)
            (output / 'runner.stderr.log').write_bytes(result.stderr)
            status = 'passed' if result.returncode == 0 else 'failed'
            print(result.stdout.decode('utf-8',errors='replace'))
            if result.returncode:
                raise subprocess.CalledProcessError(result.returncode,['node',str(script)])
            return 0
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    finally:
        record_run(state_dir,output,kind='stand',manifest=manifest,status=status)


if __name__ == '__main__':
    raise SystemExit(main())
