"""Keep detached orchestration on the launching Core, not the editable checkout."""

import hashlib
import importlib.metadata
from pathlib import Path
import sys


def worker_core_identity() -> dict[str, str]:
    package = Path(__file__).resolve().parents[2]
    files = {
        'worker_sha256': package / 'services/builder/automation_worker.py',
        'orchestrator_sha256': package / 'services/builder/automation.py',
        'ui_catalog_sha256': package / 'abi/ui.capability_catalog.v1.json',
    }
    return {
        'source_root': str(package.parent),
        **{key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in files.items()},
    }


def worker_runtime_identity() -> dict:
    try:
        automation = importlib.metadata.version('adaos-automation')
    except importlib.metadata.PackageNotFoundError:
        automation = None
    return {'python_executable': sys.executable, 'python_prefix': sys.prefix,
            'automation_version': automation}
