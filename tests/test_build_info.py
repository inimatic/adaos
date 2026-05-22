from __future__ import annotations

from pathlib import Path

from adaos import build_info


def test_base_version_reads_pyproject(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ADAOS_BASE_VERSION", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )

    assert build_info.base_version(tmp_path) == "2.3.4"


def test_base_version_env_override_wins(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADAOS_BASE_VERSION", "9.8.7")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )

    assert build_info.base_version(tmp_path) == "9.8.7"


def test_compute_version_uses_pyproject_base(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ADAOS_BASE_VERSION", raising=False)
    monkeypatch.delenv("ADAOS_BUILD_VERSION", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(build_info, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        build_info,
        "_git",
        lambda *args: {"rev-list": "42", "rev-parse": "abc1234"}.get(args[0]),
    )

    assert build_info._compute_version() == "2.3.4+42.abc1234"
