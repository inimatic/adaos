"""Keep detached orchestration on the launching Core, not the editable checkout."""

import hashlib
from pathlib import Path


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
