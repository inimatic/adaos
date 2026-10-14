"""Export the common public C2/SDK projection for the independently deployed Root.

No Workspace, application, subnet, credentials or live state is exported.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.contract_projections import project_context_units, publish_context_units
from adaos.services.root_mcp.descriptor_search import get_descriptor_item

SYMBOLS = ['adaos.sdk.builder.semantic_ui.admit', 'adaos.sdk.resources.query']


def export_root_context():
    projection = project_context_units(sdk_symbols=SYMBOLS)
    publish_context_units(projection)
    items = []
    for unit in projection['units']:
        result = get_descriptor_item(**unit['locator'])
        # Receipt timestamps describe the local read, not static platform facts.
        items.append({'locator': unit['locator'], 'result': {key: result[key] for key in
            ('schema', 'descriptor_id', 'item_id', 'level', 'item')}})
    value = {'schema': 'adaos.root.context_publication.v1', 'projection': projection, 'items': items}
    value['bundle_digest'] = canonical_payload_digest(value)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    value = export_root_context()
    text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding='utf-8') != text:
            raise SystemExit('Root context publication is stale; regenerate from the declared Core/Client source')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    print(json.dumps({'bundle_digest': value['bundle_digest'], 'projection_digest': value['projection']['projection_digest'],
        'items': len(value['items'])}))


if __name__ == '__main__':
    main()
