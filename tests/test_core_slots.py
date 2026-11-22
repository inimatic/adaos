from __future__ import annotations

import os
from pathlib import Path

import pytest

from adaos.services import core_slots


def test_active_slot_read_does_not_create_slot_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(core_slots, "_base_dir", lambda: tmp_path)

    assert core_slots.active_slot() is None
    assert not (tmp_path / "state" / "core_slots").exists()


def test_validate_slot_structure_reports_nested_slot_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    slot_root = core_slots.slot_dir("A")
    (slot_root / "A").mkdir(parents=True, exist_ok=True)

    payload = core_slots.validate_slot_structure("A")

    assert payload["ok"] is False
    assert any("nested_slot_dir:" in item for item in payload["issues"])
    assert any(item == "missing_manifest" for item in payload["issues"])


def test_validate_slot_structure_reports_valid_slot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    slot_root = core_slots.slot_dir("B")
    repo_dir = slot_root / "repo" / "src" / "adaos" / "apps"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "autostart_runner.py").write_text("print('ok')\n", encoding="utf-8")
    python_rel = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    python_path = slot_root / "venv" / python_rel
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("#!/usr/bin/env python\n", encoding="utf-8")
    core_slots.write_slot_manifest(
        "B",
        {
            "slot": "B",
            "repo_dir": str(slot_root / "repo"),
            "venv_dir": str(slot_root / "venv"),
        },
    )

    payload = core_slots.validate_slot_structure("B")

    assert payload["ok"] is True
    assert payload["issues"] == []


def test_active_slot_prefers_process_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    core_slots.activate_slot("A")
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT", "B")

    assert core_slots.active_slot() == "B"


def test_slot_marker_write_is_atomic_when_replace_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", str(tmp_path))
    marker = tmp_path / "state" / "core_slots" / "active"
    core_slots._write_text(marker, "A")

    monkeypatch.setattr(
        core_slots.os,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        core_slots._write_text(marker, "B")

    assert marker.read_text(encoding="utf-8") == "A"
    assert list(marker.parent.glob(".active.*")) == []


@pytest.mark.parametrize('damage', ['missing_manifest', 'missing_python', 'missing_entry', 'escaped_repo'])
def test_manual_rollback_preserves_selection_when_previous_executable_is_broken(monkeypatch, tmp_path, damage):
    from test_core_update import _make_valid_slot
    monkeypatch.setenv('ADAOS_BASE_DIR', str(tmp_path))
    _make_valid_slot('A')
    core_slots.activate_slot('A')
    core_slots.activate_slot('B')
    if damage == 'missing_manifest':
        core_slots.slot_manifest_path('A').unlink()
    elif damage == 'missing_python':
        (core_slots.slot_dir('A') / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')).unlink()
    elif damage == 'missing_entry':
        (core_slots.slot_dir('A') / 'repo/src/adaos/apps/autostart_runner.py').unlink()
    else:
        external = tmp_path / 'outside-slot'
        external.mkdir()
        manifest = core_slots.read_slot_manifest('A')
        core_slots.write_slot_manifest('A', {**manifest, 'repo_dir': str(external)})
    with pytest.raises(ValueError, match='rollback_slot_structure_invalid'):
        core_slots.rollback_to_previous_slot()
    assert core_slots.selected_slot() == 'B' and core_slots.previous_slot() == 'A'
