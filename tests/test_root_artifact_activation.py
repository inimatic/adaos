from __future__ import annotations

import os
from pathlib import Path

import pytest

from adaos.services.root import service as root_service


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_directory_activation_falls_back_when_live_directory_is_locked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "builder_skill"
    staged = tmp_path / ".builder_skill.staged"
    _write(target / "handlers" / "main.py", "old")
    _write(target / "obsolete.txt", "remove")
    _write(target / "__pycache__" / "main.pyc", "runtime-cache")
    _write(staged / "handlers" / "main.py", "new")
    _write(staged / "workflow.json", "{}")
    original_replace = Path.replace

    def locked_directory_replace(path: Path, destination: Path) -> Path:
        if path == target:
            raise PermissionError(32, "directory is in use", str(path))
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", locked_directory_replace)

    root_service._replace_directory_transactionally(staged, target)

    assert (target / "handlers" / "main.py").read_text(encoding="utf-8") == "new"
    assert (target / "workflow.json").read_text(encoding="utf-8") == "{}"
    assert not (target / "obsolete.txt").exists()
    assert (target / "__pycache__" / "main.pyc").read_text(encoding="utf-8") == "runtime-cache"
    assert not staged.exists()
    assert not list(tmp_path.glob(".builder_skill.rollback-*"))


def test_file_atomic_activation_rolls_back_after_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "builder_skill"
    staged = tmp_path / ".builder_skill.staged"
    _write(target / "handlers" / "main.py", "old")
    _write(target / "skill.yaml", "version: old\n")
    _write(staged / "handlers" / "main.py", "new")
    _write(staged / "skill.yaml", "version: new\n")
    original_replace = os.replace
    failed = False

    def fail_once(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        nonlocal failed
        destination_path = Path(destination)
        source_path = Path(source)
        if not failed and destination_path.name == "skill.yaml" and ".publish-" in source_path.name:
            failed = True
            raise PermissionError(32, "file is in use", str(destination_path))
        original_replace(source, destination)

    monkeypatch.setattr(root_service.os, "replace", fail_once)

    with pytest.raises(PermissionError):
        root_service._replace_directory_contents_transactionally(staged, target)

    assert (target / "handlers" / "main.py").read_text(encoding="utf-8") == "old"
    assert (target / "skill.yaml").read_text(encoding="utf-8") == "version: old\n"
    assert not staged.exists()
    assert not list(tmp_path.glob(".builder_skill.rollback-*"))
