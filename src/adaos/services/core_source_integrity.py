"""Seal executable Core inputs independently of Git labels and download receipts."""
from __future__ import annotations

import hashlib
import ast
from pathlib import Path

from adaos.services.bootstrap_update import BOOTSTRAP_CRITICAL_PATHS, core_build_inputs, core_runtime_inputs

LEGACY_SCHEMA = 'adaos.core.source_integrity.v1'
SCHEMA = 'adaos.core.source_integrity.v2'


def _submodule_config_digest(root: Path) -> str | None:
    config = root / '.gitmodules'
    return 'sha256:' + hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else None


def source_integrity_for_checkout(repo: Path) -> dict:
    """A new controller must emit a seal the target runtime can actually read.

    Read literal, versioned capabilities without importing candidate code into
    the controller. Earlier seal-capable Core declares only SCHEMA v1; current
    Core also declares its backward-compatible LEGACY_SCHEMA.
    """
    module = Path(repo) / 'src/adaos/services/core_source_integrity.py'
    if not module.exists():
        return source_integrity_receipt(repo, schema=LEGACY_SCHEMA)
    if module.stat().st_size > 65536:
        raise ValueError('core_source_integrity_target_capability_invalid')
    try:
        tree = ast.parse(module.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeError) as exc:
        raise ValueError('core_source_integrity_target_capability_invalid') from exc
    declared = {node.value.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id in {'SCHEMA', 'LEGACY_SCHEMA'} for target in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)}
    schema = next((value for value in (SCHEMA, LEGACY_SCHEMA) if value in declared), None)
    if schema is None:
        raise ValueError('core_source_integrity_target_capability_invalid')
    return source_integrity_receipt(repo, schema=schema)


def source_integrity_receipt(repo: Path, *, schema: str = SCHEMA) -> dict:
    if schema not in {SCHEMA, LEGACY_SCHEMA}:
        raise ValueError('core_source_integrity_receipt_invalid')
    root = Path(repo).resolve()
    source = root / 'src/adaos'
    paths = set(core_runtime_inputs(source, include_native_component=schema == LEGACY_SCHEMA))
    paths.update(path for path in core_build_inputs(root) if path.exists())
    paths.update(root / name for name in BOOTSTRAP_CRITICAL_PATHS if (root / name).is_file())
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda path: path.relative_to(root).as_posix()):
        # No extra import directory or file may silently escape the sealed tree.
        if not path.is_file() or path.absolute() != path.resolve():
            raise ValueError('core_source_integrity_path_invalid')
        digest.update(path.relative_to(root).as_posix().encode('utf-8') + b'\0')
        with path.open('rb') as stream:
            digest.update(hashlib.file_digest(stream, 'sha256').digest())
    if not paths:
        raise ValueError('core_source_integrity_inputs_missing')
    return {'schema': schema, 'digest': 'sha256:' + digest.hexdigest(), 'files': len(paths),
        'submodule_config_digest': _submodule_config_digest(root)}


def verify_slot_source_integrity(manifest: dict) -> bool | None:
    """Legacy slots remain explicit legacy; a supplied seal must always verify."""
    if 'source_integrity' not in manifest:
        return None
    expected = manifest['source_integrity']
    if (not isinstance(expected, dict) or expected.get('schema') not in {SCHEMA, LEGACY_SCHEMA}
            or not manifest.get('repo_dir')):
        raise ValueError('core_source_integrity_receipt_invalid')
    # Validate traversal authority before enumerating: a missing/changed
    # .gitmodules must not turn this read into a scan of every foreign checkout.
    if _submodule_config_digest(Path(manifest['repo_dir'])) != expected.get('submodule_config_digest'):
        raise ValueError('core_source_integrity_changed')
    if source_integrity_receipt(Path(manifest['repo_dir']), schema=expected['schema']) != expected:
        raise ValueError('core_source_integrity_changed')
    return True
