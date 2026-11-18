"""Generate archive pin metadata from the staged integration gitlinks."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess


def synchronize(root: Path, *, check: bool = False) -> list[str]:
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args]).decode('utf-8')
    metadata = git('ls-files', '-z', '--', 'src/adaos/integrations/*.sha').split('\0')
    changed = []
    for relative in filter(None, metadata):
        integration = relative.removesuffix('.sha')
        rows = git('ls-files', '--stage', '--', integration).splitlines()
        if len(rows) != 1:
            raise ValueError('Integration must have one resolved staged gitlink: ' + integration)
        fields = rows[0].split(maxsplit=3)
        if fields[0] != '160000' or fields[2] != '0' or not re.fullmatch('[a-f0-9]{40}|[a-f0-9]{64}', fields[1]):
            raise ValueError('Integration pin is not an exact resolved gitlink: ' + integration)
        path = root / relative
        if path.is_symlink():
            raise ValueError('Integration metadata must be a regular tracked file')
        expected = fields[1] + '\n'
        if path.read_text(encoding='ascii') != expected:
            changed.append(relative)
            if not check:
                path.write_text(expected, encoding='ascii', newline='\n')
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    changed = synchronize(args.repo_root.resolve(), check=args.check)
    for path in changed:
        print(('Stale generated pin: ' if args.check else 'Updated generated pin: ') + path)
    return int(args.check and bool(changed))


if __name__ == '__main__':
    raise SystemExit(main())
