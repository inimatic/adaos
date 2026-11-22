from pathlib import Path

import pytest

from adaos.services.core_source_integrity import source_integrity_receipt, verify_slot_source_integrity


@pytest.fixture
def sealed(tmp_path):
    root = tmp_path / 'repo'
    files = {'src/adaos/apps/autostart_runner.py': 'print("entry")',
        '.gitmodules': '[submodule "client"]\npath = src/adaos/integrations/client\n',
        'src/adaos/abi/example.json': '{"type":"object"}', 'pyproject.toml': '[project]\nversion="1.0.0"',
        'uv.lock': 'version=1', '.github/release-compatibility.json': '{"data":{}}',
        'src/adaos/integrations/client.sha': 'a' * 40}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return root, {'repo_dir': str(root), 'source_integrity': source_integrity_receipt(root)}


def test_seal_does_not_depend_on_slot_location_and_keeps_dependencies_out(sealed, tmp_path):
    import shutil
    root, manifest = sealed
    assert verify_slot_source_integrity(manifest) is True
    for name in ['src/adaos/__pycache__/example.pyc', 'src/adaos/integrations/client/node_modules/cache']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('generated')
    assert verify_slot_source_integrity(manifest) is True
    relocated = tmp_path / 'another-slot'
    shutil.copytree(root, relocated)
    assert source_integrity_receipt(relocated) == manifest['source_integrity']


@pytest.mark.parametrize('name', ['src/adaos/apps/autostart_runner.py', 'src/adaos/abi/example.json',
    'pyproject.toml', 'uv.lock', '.gitmodules', '.github/release-compatibility.json', 'src/adaos/integrations/client.sha'])
@pytest.mark.parametrize('damage', ['edit', 'delete'])
def test_existing_executable_configuration_and_resource_changes_invalidate_seal(sealed, name, damage):
    root, manifest = sealed
    if damage == 'edit':
        (root / name).write_text('changed', encoding='utf-8')
    else:
        (root / name).unlink()
    with pytest.raises(ValueError, match='core_source_integrity_changed'):
        verify_slot_source_integrity(manifest)


def test_new_importable_module_cannot_hide_behind_the_original_commit(sealed):
    root, manifest = sealed
    (root / 'src/adaos/apps/new_module.py').write_text('extra')
    with pytest.raises(ValueError, match='core_source_integrity_changed'):
        verify_slot_source_integrity(manifest)


def test_boot_source_dirty_flag_uses_real_seal_instead_of_manifest_label(sealed, monkeypatch):
    from adaos import build_info
    root, manifest = sealed
    manifest['source_dirty'] = False
    monkeypatch.setattr(build_info, '_active_slot_manifest', lambda: manifest)
    assert build_info._compute_source_dirty() is False
    (root / 'src/adaos/abi/example.json').write_text('changed')
    assert build_info._compute_source_dirty() is True


def test_legacy_and_invalid_receipts_are_distinct():
    assert verify_slot_source_integrity({'git_commit': 'a' * 40}) is None
    with pytest.raises(ValueError, match='core_source_integrity_receipt_invalid'):
        verify_slot_source_integrity({'source_integrity': None})


def test_changed_submodule_config_is_rejected_before_foreign_tree_enumeration(sealed, monkeypatch):
    from adaos.services import core_source_integrity as integrity
    root, manifest = sealed
    (root / '.gitmodules').unlink()
    monkeypatch.setattr(integrity, 'core_runtime_inputs', lambda source, **kwargs: pytest.fail('foreign source traversal'))
    with pytest.raises(ValueError, match='core_source_integrity_changed'):
        verify_slot_source_integrity(manifest)


def test_source_input_policy_evolution_keeps_previous_slots_verifiable(sealed):
    from adaos.services.core_source_integrity import LEGACY_SCHEMA
    root, current = sealed
    native = root / 'src/adaos/integrations/android-node/native-source.kt'
    native.parent.mkdir(parents=True)
    native.write_text('separately built native source')
    legacy = {'repo_dir': str(root), 'source_integrity': source_integrity_receipt(root, schema=LEGACY_SCHEMA)}
    assert verify_slot_source_integrity(current) is True
    assert verify_slot_source_integrity(legacy) is True
    native.write_text('changed native input')
    assert verify_slot_source_integrity(current) is True
    with pytest.raises(ValueError, match='core_source_integrity_changed'):
        verify_slot_source_integrity(legacy)


@pytest.mark.parametrize('target', ['old', 'current', 'unsupported'])
def test_controller_seals_for_target_runtime_capabilities_without_importing_it(sealed, target):
    from adaos.services.core_source_integrity import LEGACY_SCHEMA, SCHEMA, source_integrity_for_checkout
    root, _ = sealed
    module = root / 'src/adaos/services/core_source_integrity.py'
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("raise AssertionError('candidate code must not be imported')\n" + (
        f'SCHEMA={LEGACY_SCHEMA!r}\n' if target == 'old' else
        f'SCHEMA={SCHEMA!r}\nLEGACY_SCHEMA={LEGACY_SCHEMA!r}\n' if target == 'current' else
        "SCHEMA='unrecognized-format'\n"))
    if target == 'unsupported':
        with pytest.raises(ValueError, match='core_source_integrity_target_capability_invalid'):
            source_integrity_for_checkout(root)
    else:
        receipt = source_integrity_for_checkout(root)
        assert receipt['schema'] == (LEGACY_SCHEMA if target == 'old' else SCHEMA)
        assert verify_slot_source_integrity({'repo_dir': str(root), 'source_integrity': receipt}) is True


def test_runtime_launch_refuses_drift_before_preparing_process_arguments(sealed):
    from types import SimpleNamespace
    from adaos.apps.supervisor_runtime import ProcessSupervisor
    root, manifest = sealed
    (root / 'src/adaos/abi/example.json').write_text('changed after preparation')
    operations = SimpleNamespace(read_slot_manifest=lambda slot: manifest)
    with pytest.raises(ValueError, match='core_source_integrity_changed'):
        ProcessSupervisor(None).runtime_launch_spec(SimpleNamespace(), operations, slot='B')


def test_source_outside_the_tree_cannot_be_added_through_a_link(sealed, tmp_path):
    root, manifest = sealed
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'escape.py').write_text('external')
    link = root / 'src/adaos/escaped'
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        if __import__('os').name != 'nt':
            raise
        import subprocess
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(external)], capture_output=True)
        assert result.returncode == 0
    with pytest.raises(ValueError, match='core_source_integrity_path_invalid'):
        verify_slot_source_integrity(manifest)
