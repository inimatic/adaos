"""Generate or verify Root's immutable public Prototype guidance projection."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from adaos.services.builder.prototype_mcp import library_snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--retain-from-git", help="Retain one reviewed prior backend commit's exact public library")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] /
        "src/adaos/integrations/adaos-backend/backend/prototype-context-library.json")
    args = parser.parse_args()
    snapshot = library_snapshot()
    if args.retain_from_git:
        if args.check or not re.fullmatch(r'[a-f0-9]{40}', args.retain_from_git):
            raise SystemExit('Retention requires a full reviewed backend commit and a write invocation')
        previous = json.loads(subprocess.check_output(['git', 'show', args.retain_from_git +
            ':backend/prototype-context-library.json'], cwd=args.output.parent.parent, text=True, encoding='utf-8'))
        retained_path = args.output.with_name('prototype-context-library.retained.json')
        retained = json.loads(retained_path.read_text(encoding='utf-8')) if retained_path.exists() else {
            'schema': 'adaos.builder.prototype_context_versions.v1', 'snapshots': []}
        values = {row['library_digest']: row for row in retained['snapshots']}
        values[previous['library_digest']] = previous
        values.pop(snapshot['library_digest'], None)
        if len(values) > 3:
            raise SystemExit('At most three prior libraries; explicitly review retirement before adding another')
        retained['snapshots'] = list(values.values())
        retained_path.write_text(json.dumps(retained, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    if args.check:
        if json.loads(args.output.read_text(encoding="utf-8")) != snapshot:
            raise SystemExit("Root Prototype context projection differs from its Core authority")
    else:
        args.output.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"checked": args.check, "library_digest": snapshot["library_digest"],
                      "fragments": len(snapshot["fragments"])}))


if __name__ == "__main__":
    main()
