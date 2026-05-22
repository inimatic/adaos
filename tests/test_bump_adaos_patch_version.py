from __future__ import annotations

from pathlib import Path

import pytest

from tools.bump_adaos_patch_version import bump_patch, main, read_project_version


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


def test_main_current_does_not_update_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")

    assert main(["--pyproject", str(pyproject), "--current"]) == 0

    assert capsys.readouterr().out.strip() == "0.1.0"
    assert read_project_version(pyproject) == "0.1.0"
