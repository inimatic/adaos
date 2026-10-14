"""Run private-consumer gates without publishing source or captured diagnostics.

The command's real exit code is preserved. Raw logs and JUnit stay local to the
ephemeral runner; only counts and a content-free receipt may be published.
"""
import argparse
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def public_junit(source: Path, destination: Path) -> dict:
    original = ET.parse(source).getroot()
    result = ET.Element('testsuites')
    counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    for suite in original.iter('testsuite'):
        clean = ET.SubElement(result, 'testsuite', name='private-contract-checks')
        suite_counts = dict.fromkeys(counts, 0)
        for case in suite.findall('testcase'):
            # Opaque IDs avoid leaking parameter values, paths or code in names.
            suite_counts['tests'] += 1
            try:
                duration = float(case.get('time', '0'))
                duration = duration if math.isfinite(duration) and duration >= 0 else 0
            except ValueError:
                duration = 0
            item = ET.SubElement(clean, 'testcase', name=f'case-{counts["tests"] + suite_counts["tests"]}',
                                 classname='contract', time=str(duration))
            for tag, counter in (('failure', 'failures'), ('error', 'errors'), ('skipped', 'skipped')):
                if case.find(tag) is not None:
                    suite_counts[counter] += 1
                    ET.SubElement(item, tag, message='Private diagnostic withheld')
        for key, value in suite_counts.items():
            clean.set(key, str(value))
            counts[key] += value
    destination.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(result).write(destination, encoding='utf-8', xml_declaration=True)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--cwd', type=Path, default=ROOT)
    parser.add_argument('--junit', type=Path)
    parser.add_argument('--public-junit', type=Path)
    parser.add_argument('--require-complete', action='store_true', help='Fail if any required test was skipped')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]+', args.label):
        parser.error('label must be a safe identifier')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or bool(args.junit) != bool(args.public_junit):
        parser.error('command required; junit and public-junit must be paired')
    if args.require_complete and not args.junit:
        parser.error('require-complete requires a JUnit receipt')
    diagnostics = ROOT / '.tmp/private-contract-checks'
    diagnostics.mkdir(parents=True, exist_ok=True)
    log = diagnostics / f'{args.label}.log'
    with log.open('wb') as stream:
        try:
            status = subprocess.run(command, cwd=args.cwd, stdout=stream, stderr=subprocess.STDOUT).returncode
        except OSError:
            status = 127
    summary = {'check': args.label, 'exit_code': status}
    if args.junit:
        try:
            summary.update(public_junit(args.junit, args.public_junit))
            if args.require_complete and summary['skipped']:
                summary['receipt'] = 'incomplete'
                status = status or 1
        except (OSError, ET.ParseError):
            summary['receipt'] = 'missing-or-invalid'
            status = status or 1
    summary['exit_code'] = status
    print(json.dumps(summary))
    return status


if __name__ == '__main__':
    sys.exit(main())
