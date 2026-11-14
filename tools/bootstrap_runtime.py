"""Standard-library-only platform contracts shared by producer and installer."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import sys

PROFILES = {
    'linux-x86_64-gnu': {'os': 'linux', 'arch': 'x86_64', 'libc': 'glibc', 'min_libc': '2.31', 'docker': 'linux/amd64'},
    'linux-aarch64-gnu': {'os': 'linux', 'arch': 'aarch64', 'libc': 'glibc', 'min_libc': '2.31', 'docker': 'linux/arm64'},
    'windows-x86_64': {'os': 'windows', 'arch': 'x86_64', 'libc': 'none', 'min_libc': '0.0'},
}


def host_profile() -> str:
    arch = {'amd64': 'x86_64', 'arm64': 'aarch64'}.get(platform.machine().lower(), platform.machine().lower())
    if sys.platform == 'win32' and arch == 'x86_64':
        return 'windows-x86_64'
    if sys.platform == 'linux' and arch in ('x86_64', 'aarch64'):
        return 'linux-' + arch + '-gnu'
    raise ValueError('unsupported_managed_runtime_platform')


def runtime_lock(source: Path, profile: str) -> dict:
    value = json.loads((source / 'tools/bootstrap-runtime.lock.json').read_text(encoding='utf-8'))
    if profile == value['platform']:
        return {key: item for key, item in value.items() if key != 'profiles'}
    return {'schema': value['schema'], 'platform': profile, **value['profiles'][profile]}


def python_path(home: Path, *, venv: bool = False, profile: str | None = None) -> Path:
    windows = (profile or host_profile()).startswith('windows-')
    return home / ('Scripts/python.exe' if venv else 'python.exe') if windows else home / ('bin/python' if venv else 'bin/python3')
