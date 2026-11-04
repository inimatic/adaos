"""Fetch an exact declared Builder source; missing access is a failed gate.

No private Workspace fallback, synthetic native handler or skipped integration
test is accepted. Credentials in the CI source URL are never logged.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from adaos.services.artifact_pipeline.packages import artifact_source_snapshot, build_artifact_package
from adaos.domain.artifact_release import ArtifactSourceRef


def validate_public_source(source: Path) -> None:
    """CI publications contain code and tests, never private authoring state."""
    from adaos.services.artifact_pipeline.packages import _EXCLUDED_FILES
    operational = sorted(name for name in _EXCLUDED_FILES if (source/name).exists())
    operational += [name for name in ('llm_jobs', 'ui_revisions', 'artifacts') if (source/name).exists()]
    if operational:
        raise SystemExit('Published Builder CI source contains private operational state: ' + ', '.join(operational))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pin', type=Path, default=ROOT/'e2e/builder/development/contract-conformance/builder-source-pin.json')
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    pin = json.loads(args.pin.read_text(encoding='utf-8'))
    source = args.source
    if source is None:
        if pin.get('source_state') == 'local_beta_pending_publication':
            raise SystemExit('Builder Beta is qualified locally but its source checkpoint is not published. '
                'Publish that exact checkpoint before resuming CI, or explicitly supply --source '
                'for a verified local gate. No older Builder or Workspace fallback is allowed.')
        checkout = args.checkout.resolve()
        if not checkout.is_relative_to((ROOT/'.tmp').resolve()):
            raise SystemExit('CI source checkout must be inside repository .tmp')
        checkout.mkdir(parents=True, exist_ok=False)
        url = os.environ.get('BUILDER_SOURCE_URL') or pin['repository']
        commands = [['init'], ['remote', 'add', 'origin', url], ['fetch', '--depth=1', 'origin', pin['revision']],
            ['checkout', '--detach', 'FETCH_HEAD']]
        for command in commands:
            result = subprocess.run(['git', '-C', str(checkout), *command], capture_output=True)
            if result.returncode:
                raise SystemExit('Exact Builder source fetch failed; configure BUILDER_SOURCE_URL access. No native gate was skipped.')
        source = checkout/pin['source_path']
        validate_public_source(source)
    package = build_artifact_package(source, kind='skill', source_ref=ArtifactSourceRef(
        forge='local', repository='declared-builder-source', revision=artifact_source_snapshot(source)['digest'],
        path_scope=(pin['source_path']+'/',)))
    if package.ref.digest != pin['package_digest']:
        raise SystemExit('Builder source package differs from its pinned CI qualification')
    print(json.dumps({'builder_source': str(source.resolve()), 'package_digest': package.ref.digest, 'version': pin['version']}))
    if os.environ.get('GITHUB_ENV'):
        with open(os.environ['GITHUB_ENV'], 'a', encoding='utf-8') as stream:
            stream.write('ADAOS_TEST_BUILDER_SOURCE=' + str(source.resolve()) + '\n')
            stream.write('ADAOS_TEST_BUILDER_PIN=' + str(args.pin.resolve()) + '\n')


if __name__ == '__main__':
    main()
