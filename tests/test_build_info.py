from __future__ import annotations

from pathlib import Path

from adaos import build_info


def _clear_build_env(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_BASE_VERSION", raising=False)
    monkeypatch.delenv("ADAOS_BUILD_VERSION", raising=False)
    monkeypatch.delenv("ADAOS_BUILD_DATE", raising=False)
    monkeypatch.delenv("ADAOS_GIT_COMMIT", raising=False)
    monkeypatch.delenv("ADAOS_ACTIVE_CORE_SLOT_DIR", raising=False)
    monkeypatch.delenv("ADAOS_SLOT_REPO_ROOT", raising=False)
    monkeypatch.delenv("ADAOS_BASE_DIR", raising=False)
    monkeypatch.delenv("ADAOS_ACTIVE_CORE_SLOT", raising=False)


def test_base_version_reads_pyproject(monkeypatch, tmp_path: Path) -> None:
    _clear_build_env(monkeypatch)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )

    assert build_info.base_version(tmp_path) == "2.3.4"


def test_base_version_checkout_wins_over_inherited_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADAOS_BASE_VERSION", "9.8.7")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "adaos"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )

    assert build_info.base_version(tmp_path) == "2.3.4"


def test_base_version_env_override_is_used_for_packaged_layout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADAOS_BASE_VERSION", "9.8.7")

    assert build_info.base_version(tmp_path) == "9.8.7"


def test_compute_version_uses_pyproject_base(monkeypatch, tmp_path: Path) -> None:
    _clear_build_env(monkeypatch)
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


def test_build_info_uses_active_slot_manifest_without_parent_git_discovery(monkeypatch, tmp_path: Path) -> None:
    _clear_build_env(monkeypatch)
    slot_dir = tmp_path / "state" / "core_slots" / "slots" / "B"
    slot_dir.mkdir(parents=True)
    (slot_dir / "manifest.json").write_text(
        (
            '{"base_version":"0.1.391",'
            '"build_version":"0.1.391+1.6076dcd",'
            '"build_date":"2026-06-23T14:05:15+00:00",'
            '"git_commit":"6076dcd123456789"}'
        ),
        encoding="utf-8",
    )
    package_root = tmp_path / "venv" / "lib" / "python3.11" / "site-packages"
    package_root.mkdir(parents=True)
    monkeypatch.setenv("ADAOS_ACTIVE_CORE_SLOT_DIR", str(slot_dir))
    monkeypatch.setattr(build_info, "_repo_root", lambda: package_root)
    monkeypatch.setattr(
        build_info,
        "_git",
        lambda *args: (_ for _ in ()).throw(AssertionError("slot identity must not probe parent Git")),
    )
    monkeypatch.setattr(build_info, "_installed_distribution_version", lambda: None)

    assert build_info.base_version() == "0.1.391"
    assert build_info._compute_version() == "0.1.391+1.6076dcd"
    assert build_info._compute_build_date() == "2026-06-23T14:05:15+00:00"
    assert build_info._compute_git_commit() == "6076dcd123456789"


def test_compute_git_commit_prefers_explicit_identity(monkeypatch) -> None:
    _clear_build_env(monkeypatch)
    monkeypatch.setenv("ADAOS_GIT_COMMIT", "abcdef123456")
    monkeypatch.setattr(build_info, "_git", lambda *args: "ignored")

    assert build_info._compute_git_commit() == "abcdef123456"
