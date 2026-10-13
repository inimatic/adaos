"""Freeze a declared Builder source and qualify its package without live activation."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from adaos.services.builder.delivery_qualification import qualify_delivery

parser = argparse.ArgumentParser(__doc__)
parser.add_argument('--builder-source', type=Path, required=True)
parser.add_argument('--active-runtime', type=Path)
parser.add_argument('--profile', choices=['semantic-authoring', 'message-boundary'], default='semantic-authoring')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
manifest = qualify_delivery(repo_root=ROOT, builder_source=args.builder_source,
    package_store=args.output / 'package-store', materialization_target=args.output / 'materialized-builder',
    active_runtime=args.active_runtime, profile=args.profile)
(args.output / 'delivery-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'manifest_digest': manifest['manifest_digest'], 'builder_version': manifest['builder']['package']['version'],
    'model_calls': 0, 'qualification_scope': manifest['qualification_scope']}))
