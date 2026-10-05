"""Sequential live Management reads with bounded, payload-free timing evidence.

The first request is first-touch, not proof of process/storage-cold execution.
Do not add concurrent calls: this measures the single-admission read model.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import psutil
import requests

from adaos.apps.cli.active_control import resolve_control_token


def parse_server_timing(value: str) -> dict[str, float]:
    result = {}
    for name, duration in re.findall(r'(?:^|,)\s*([\w-]+)\s*;\s*dur=([0-9.]+)', value):
        try:
            number = float(duration)
        except ValueError:
            continue
        if math.isfinite(number) and number >= 0:
            result[name] = number
    return result


def summarize(samples: list[dict]) -> dict:
    series: dict[str, list[float]] = defaultdict(list)
    for item in samples:
        for name, value in {'wall_ms': item['wall_ms'], 'response_bytes': item['response_bytes'],
                            **item.get('server_timing_ms', {})}.items():
            series[name].append(value)
    return {
        name: {'n': len(values), 'p50': sorted(values)[math.ceil(len(values) * .5) - 1],
               'p95': sorted(values)[math.ceil(len(values) * .95) - 1]}
        for name, values in series.items() if values
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://127.0.0.1:8778')
    parser.add_argument('--webspace', default='desktop')
    parser.add_argument('--dev', action='store_true')
    parser.add_argument('--samples', type=int, default=7)
    parser.add_argument('--sections', nargs='+', choices=['system_bootstrap', 'node_dashboard'],
                        default=['system_bootstrap', 'node_dashboard'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.samples <= 30:
        parser.error('samples must be 1..30')
    output = args.output.resolve()
    artifact_root = (Path(__file__).resolve().parents[1] / 'e2e/artifacts').resolve()
    if not output.is_relative_to(artifact_root) or output == artifact_root:
        parser.error('output must be a file inside e2e/artifacts')
    if output.exists():
        parser.error('retain previous evidence; choose a new output')
    base = args.api.rstrip('/')
    session = requests.Session()
    session.trust_env = False
    session.headers['X-AdaOS-Token'] = resolve_control_token(base_url=base)
    report = {'schema': 'adaos.management_read_model_benchmark.v1',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'first-touch then warm sequential reads; existing process and data; empirical percentiles',
              'webspace': args.webspace, 'dev': args.dev, 'sections': {}, 'ok': True}
    for section in args.sections:
        samples = []
        for index in range(args.samples):
            started = time.perf_counter()
            row = {'sample': index + 1, 'first_touch': index == 0,
                   'host_memory_percent': psutil.virtual_memory().percent,
                   'host_cpu_percent': psutil.cpu_percent(interval=None),
                   'response_bytes': 0}
            try:
                response = session.post(base + '/api/tools/call', timeout=75, json={
                    'tool': 'web_desktop_runtime_skill:get_system_overview', 'dev': args.dev,
                    'arguments': {'webspace_id': args.webspace, 'section': section},
                    'context': {'webspace_id': args.webspace}, 'timeout': 60,
                })
                body = response.json()
                result = body.get('result', body)
                row.update(status=response.status_code, response_bytes=len(response.content),
                           server_timing_ms=parse_server_timing(response.headers.get('Server-Timing', '')),
                           ok=response.ok and isinstance(result, dict) and result.get('ok') is not False)
                if isinstance(result, dict):
                    row['subscription_status'] = result.get('subscription_status')
                    row['hardware_metric_count'] = len(result.get('hardware_metrics') or [])
            except (requests.RequestException, ValueError) as exc:
                row.update(ok=False, error=type(exc).__name__)
            row['wall_ms'] = round((time.perf_counter() - started) * 1000, 1)
            samples.append(row)
            print(json.dumps({'section': section, **row}), flush=True)
        report['sections'][section] = {'samples': samples, 'all': summarize(samples),
                                       'warm': summarize(samples[1:])}
        report['ok'] = report['ok'] and all(item['ok'] for item in samples)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({name: value['all'] for name, value in report['sections'].items()}))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
