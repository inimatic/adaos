from __future__ import annotations

from pathlib import Path

import pytest

from tools.bump_adaos_patch_version import (
    bump_patch,
    main,
    read_project_version,
    write_uv_lock_project_version,
)


def test_bump_patch_increments_plain_semver() -> None:
    assert bump_patch("1.2.3") == "1.2.4"


def test_bump_patch_rejects_non_plain_semver() -> None:
    with pytest.raises(RuntimeError, match="expected plain"):
        bump_patch("1.2.3+4.abc")


def test_main_updates_project_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[build-system]\nrequires = []\n\n[project]\nname = "adaos"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    assert main(["--pyproject", str(pyproject)]) == 0

    assert capsys.readouterr().out.strip() == "0.1.1"
    assert read_project_version(pyproject) == "0.1.1"


def test_main_keeps_editable_project_version_in_uv_lock_synchronized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    uv_lock = tmp_path / "uv.lock"
    pyproject.write_text('[project]\nname = "adaos"\nversion = "0.1.0"\n', encoding="utf-8")
    uv_lock.write_text(
        'version = 1\n\n[[package]]\nname = "adaos"\nversion = "0.1.0"\nsource = { editable = "." }\n',
        encoding="utf-8",
    )

    assert main(["--pyproject", str(pyproject)]) == 0

    assert capsys.readouterr().out.strip() == "0.1.1"
    assert 'version = "0.1.1"' in uv_lock.read_text(encoding="utf-8")


def test_write_uv_lock_project_version_rejects_missing_editable_project(tmp_path: Path) -> None:
    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text(
        'version = 1\n\n[[package]]\nname = "dependency"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="expected one editable"):
        write_uv_lock_project_version(uv_lock, "0.1.1")


def test_main_current_does_not_update_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject), "--current"]) == 0

    assert capsys.readouterr().out.strip() == "0.1.0"
    assert read_project_version(pyproject) == "0.1.0"
